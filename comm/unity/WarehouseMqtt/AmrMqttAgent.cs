// AMR 1대 ↔ MQTT. AMR 프리팹에 붙인다 (amrId = AMR01~AMR04).
//
// 발행: amr/{id}/pose 10 Hz, amr/{id}/scan2d 10 Hz (2D LiDAR 72빔), amr/{id}/state 변경 시 + 5초, amr/{id}/evt
// 구독: amr/{id}/cmd/mission, amr/{id}/cmd/vel, amr/{id}/cmd/control
//
// 주행 모드
//   scripted: 미션 경유점을 직선으로 따라간다 (경유점 t_start 전에는 그 노드 앞에서 대기 = 시간 예약 준수).
//   rl:       RL 정책(cmd/vel)의 v, w 로 움직인다. ttl(0.5초) 안에 새 명령이 없으면 정지.
//             경유점 도착(0.3 m 이내) 판정과 pick/drop 은 이 스크립트가 한다.
// pick/drop 은 IPalletPort 구현(계량대·버퍼·하차대 쪽 스크립트)에 묻는다. 없으면 시간만 보낸다.
using System.Collections.Generic;
using UnityEngine;

namespace Warehouse.Mqtt
{
    /// 정지점(SC-IN1, L1-IN, DP-OUT3 …)에서 팔레트를 주고받는 쪽이 구현.
    public interface IPalletPort
    {
        string StopId { get; }
        bool HasPallet(string pallet);          // pick 전제 조건
        bool HasSpace();                         // drop 전제 조건
        GameObject Take(string pallet);
        void Put(string pallet, GameObject palletObj);
    }

    public class AmrMqttAgent : MonoBehaviour
    {
        public string amrId = "AMR01";
        public float speed = 1.2f;              // m/s (scripted)
        public float handleSeconds = 8f;        // pick/drop
        public float battery = 90f;
        public Transform forkSocket;            // 실은 팔레트를 붙일 위치
        [Header("2D LiDAR")]
        public bool publishScan = true;
        public int beams = 72;
        public float rangeMax = 10f;
        public float scanHeight = 0.3f;
        public LayerMask scanMask = ~0;

        public string driveMode = "scripted";
        WhMqttClient _mq;
        AmrMission _mission;
        int _step, _wp;
        float _busyUntil = -1f, _waited;
        string _mode = "idle", _pallet, _node, _error;
        GameObject _palletObj;
        double? _loadKg;
        bool _paused;
        AmrVel _vel; float _velExpire;
        float _v, _w, _nextPose, _nextStateKeepalive;
        string _lastStateKey;
        static readonly Dictionary<string, IPalletPort> Ports = new Dictionary<string, IPalletPort>();

        public static void RegisterPort(IPalletPort p) => Ports[p.StopId] = p;

        void Start()
        {
            _mq = WhMqttClient.Instance;
            _mq.On(WhTopics.AmrMission(amrId), OnMission);
            _mq.On(WhTopics.AmrVel(amrId), (e, _) => { _vel = e.Data<AmrVel>(); _velExpire = Time.unscaledTime + (float)_vel.ttl; });
            _mq.On(WhTopics.AmrControl(amrId), OnControl);
        }

        // ── 명령 ────────────────────────────────────────────────────────
        void OnMission(Envelope env, string[] _)
        {
            var m = env.Data<AmrMission>();
            if (_mission != null || _error != null)
            {
                Evt("mission_rejected", m.mission_id, null, null, "busy", env.id);
                return;
            }
            _mission = m; _step = 0;
            Evt("mission_accepted", m.mission_id, null, null, null, env.id);
            StartStep();
        }

        void OnControl(Envelope env, string[] _)
        {
            var c = env.Data<AmrControl>();
            switch (c.action)
            {
                case "pause": _paused = true; break;
                case "resume": _paused = false; break;
                case "set_drive_mode": driveMode = c.drive_mode ?? "scripted"; break;
                case "cancel_mission":
                    if (_mission != null) { Evt("mission_canceled", _mission.mission_id, null, null, null); _mission = null; _mode = "idle"; }
                    break;
            }
            Evt("control_applied", _mission?.mission_id, null, null, null, env.id);
            PublishState(true);
        }

        // ── 매 프레임 ────────────────────────────────────────────────────
        void Update()
        {
            float dt = Time.deltaTime;
            battery = Mathf.Max(0f, battery - (_mode == "moving" ? 0.02f : 0.003f) * dt);
            _v = _w = 0f;
            if (!_paused)
            {
                if (driveMode == "rl") DriveByVelocity(dt);
                if (_mission != null) RunStep(dt);
            }
            if (Time.unscaledTime >= _nextPose) { _nextPose = Time.unscaledTime + 0.1f; PublishPose(); }
            PublishState(false);
        }

        void DriveByVelocity(float dt)
        {
            if (_vel == null || Time.unscaledTime > _velExpire) return;     // 명령 끊김 → 정지
            _v = (float)_vel.v; _w = (float)_vel.w;
            float theta = WhCoords.YawToTheta(transform.eulerAngles.y) + _w * dt;
            transform.rotation = WhCoords.ThetaToRotation(theta);
            var plan = WhCoords.ToPlan(transform.position) + new Vector2(Mathf.Cos(theta), Mathf.Sin(theta)) * _v * dt;
            transform.position = WhCoords.ToUnity(plan.x, plan.y, transform.position.y);
            // 실제 물리(Rigidbody, 충돌)를 쓰려면 위 두 줄 대신 Rigidbody.MovePosition / MoveRotation 사용
        }

        void StartStep()
        {
            var s = _mission.steps[_step];
            _wp = 0; _busyUntil = -1f; _waited = 0f;
            _mode = s.action == "move" ? "moving" : s.action == "pick" ? "picking" : s.action == "drop" ? "dropping"
                  : s.action == "charge" ? "charging" : "waiting";
            Evt("step_started", _mission.mission_id, s.seq, s.pallet, null);
            PublishState(true);
        }

        void FinishStep()
        {
            var s = _mission.steps[_step];
            Evt("step_done", _mission.mission_id, s.seq, s.pallet, null);
            if (++_step >= _mission.steps.Count)
            {
                var id = _mission.mission_id;
                _mission = null; _mode = "idle";
                Evt("mission_done", id, null, null, null);
                PublishState(true);
            }
            else StartStep();
        }

        void RunStep(float dt)
        {
            var s = _mission.steps[_step];
            double now = _mq.SimTime;
            switch (s.action)
            {
                case "move":
                    if (_wp >= s.path.Count) { FinishStep(); return; }
                    var wp = s.path[_wp];
                    var target = new Vector2((float)wp.x, (float)wp.y);
                    var here = WhCoords.ToPlan(transform.position);
                    if (driveMode == "scripted")
                    {
                        if (wp.t_start.HasValue && now < wp.t_start.Value && _node != wp.node) return;   // 예약 시각 전 대기
                        var next = Vector2.MoveTowards(here, target, speed * dt);
                        if ((next - here).sqrMagnitude > 1e-8f)
                            transform.rotation = WhCoords.ThetaToRotation(Mathf.Atan2(target.y - here.y, target.x - here.x));
                        transform.position = WhCoords.ToUnity(next.x, next.y, transform.position.y);
                        _v = speed;
                        here = next;
                    }
                    if (Vector2.Distance(here, target) < (driveMode == "rl" ? 0.3f : 1e-3f))
                    {
                        _node = wp.node; _wp++;
                        if (_wp >= s.path.Count)
                        {
                            if (s.theta.HasValue && driveMode == "scripted") transform.rotation = WhCoords.ThetaToRotation((float)s.theta.Value);
                            FinishStep();
                        }
                    }
                    break;
                case "pick":
                case "drop":
                    Ports.TryGetValue(s.stop ?? "", out var port);
                    if (_busyUntil < 0f)
                    {
                        bool ok = port == null || (s.action == "pick" ? port.HasPallet(s.pallet) : port.HasSpace());
                        if (!ok) { _mode = "waiting"; _waited += dt; return; }
                        _mode = s.action == "pick" ? "picking" : "dropping";
                        _busyUntil = Time.time + handleSeconds;
                        return;
                    }
                    if (Time.time < _busyUntil) return;
                    if (s.action == "pick")
                    {
                        _pallet = s.pallet;
                        _palletObj = port?.Take(s.pallet);
                        if (_palletObj && forkSocket) { _palletObj.transform.SetParent(forkSocket, false); _palletObj.transform.localPosition = Vector3.zero; }
                    }
                    else
                    {
                        if (_palletObj) _palletObj.transform.SetParent(null, true);
                        port?.Put(s.pallet, _palletObj);
                        _pallet = null; _palletObj = null; _loadKg = null;
                    }
                    FinishStep();
                    break;
                case "charge":
                    FinishStep();   // 충전기 스크립트가 battery 를 올린다 (_mode = idle 이고 충전기 위)
                    break;
                case "wait":
                    if (_busyUntil < 0f) _busyUntil = Time.time + (float)(s.duration ?? 0);
                    if (Time.time >= _busyUntil) FinishStep();
                    break;
            }
        }

        // ── 발행 ────────────────────────────────────────────────────────
        void PublishPose()
        {
            var p = WhCoords.ToPlan(transform.position);
            _mq.Send("amr.pose", new AmrPose
            {
                x = p.x, y = p.y, theta = WhCoords.YawToTheta(transform.eulerAngles.y),
                v = _v, w = _w, battery = Mathf.Round(battery * 10f) / 10f, load_kg = _loadKg, node = _node,
            }, WhTopics.AmrPose(amrId));
            if (publishScan) _mq.Send("amr.scan2d", Scan(), WhTopics.AmrScan2d(amrId));
        }

        /// 72빔, 0번 = 정면, 반시계 방향(도면 기준). Unity yaw 는 시계 방향이라 각도를 빼 간다.
        AmrScan2d Scan()
        {
            var r = new float[beams];
            var origin = transform.position + Vector3.up * scanHeight;
            float inc = 360f / beams;
            for (int k = 0; k < beams; k++)
            {
                var dir = Quaternion.Euler(0f, -k * inc, 0f) * transform.forward;
                float d = Physics.Raycast(origin, dir, out var hit, rangeMax, scanMask) ? hit.distance : rangeMax;
                r[k] = Mathf.Round(d * 1000f) / 1000f;     // mm 단위로 잘라 JSON 크기를 줄임
            }
            return new AmrScan2d { angle_min = 0, angle_inc = inc * Mathf.Deg2Rad, range_max = rangeMax, ranges = r };
        }

        void PublishState(bool force)
        {
            string mode = _paused ? "paused" : _mode;
            string key = $"{mode}|{driveMode}|{_pallet}|{_mission?.mission_id}|{_step}|{_node}|{_error}";
            if (!force && key == _lastStateKey && Time.unscaledTime < _nextStateKeepalive) return;
            _lastStateKey = key; _nextStateKeepalive = Time.unscaledTime + 5f;
            _mq.Send("amr.state", new AmrState
            {
                mode = mode, drive_mode = driveMode, battery = Mathf.Round(battery * 10f) / 10f, pallet = _pallet,
                mission_id = _mission?.mission_id, step = _mission != null ? _step : (int?)null, node = _node, error = _error,
            }, WhTopics.AmrState(amrId));
        }

        void Evt(string ev, string missionId, int? seq, string pallet, string reason, string corr = null) =>
            _mq.Send("amr.evt", new AmrEvt { @event = ev, mission_id = missionId, seq = seq, pallet = pallet, reason = reason },
                     WhTopics.AmrEvt(amrId), corr);
    }
}
