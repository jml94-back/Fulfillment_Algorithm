// 리프트·셔틀·버퍼·도크·충전기 ↔ MQTT.
//
// 리프트 (L1~L3)       구독 lift/{id}/cmd     발행 lift/{id}/state, lift/{id}/evt
// 셔틀 세트 (MS-R1-LV01) 구독 shuttle/{id}/cmd  발행 shuttle/{id}/state, shuttle/{id}/evt
// 버퍼 (L1-IN …)        발행 buffer/{id}/state  (슬롯이 바뀔 때 SetSlots 호출)
// 도크 (IN1 … OUT6)     발행 dock/{id}/state, dock/{id}/evt, (입고) scale/SC-INn/reading
// 충전기 (CHW1 …)       발행 charger/{id}/state
//
// 명령은 받은 순서(FIFO)로 하나씩 처리한다. 받으면 accepted, 시작 started, 끝나면 done / failed 를
// corr = 명령 봉투 id 로 보낸다. 장비 움직임(애니메이션)은 코루틴 자리(MoveLift, MoveMother …)에 넣는다.
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

namespace Warehouse.Mqtt
{
    public abstract class DeviceAgentBase<TCmd> : MonoBehaviour where TCmd : class
    {
        protected WhMqttClient Mq;
        readonly Queue<(TCmd cmd, string corr)> _queue = new Queue<(TCmd, string)>();
        protected TCmd Current;
        protected string CurrentCorr;
        protected abstract string DeviceId { get; }
        protected abstract string CmdTopic { get; }
        protected abstract string EvtTopic { get; }
        protected abstract string EvtType { get; }
        protected abstract (string cmdId, string jobId, string pallet, string action) Describe(TCmd c);
        protected abstract IEnumerator Execute(TCmd c);   // 끝나면 Done() 또는 Fail()
        protected abstract void PublishState();
        protected int QueueCount => _queue.Count;

        protected virtual void Start()
        {
            Mq = WhMqttClient.Instance;
            Mq.On(CmdTopic, (env, _) =>
            {
                var c = env.Data<TCmd>();
                var d = Describe(c);
                if (d.action == "cancel") { Evt("canceled", c, env.id); return; }
                _queue.Enqueue((c, env.id));
                Evt("accepted", c, env.id);
                PublishState();
            });
            PublishState();
        }

        protected virtual void Update()
        {
            if (Current != null || _queue.Count == 0) return;
            (Current, CurrentCorr) = _queue.Dequeue();
            Evt("started", Current, CurrentCorr);
            StartCoroutine(Execute(Current));
            PublishState();
        }

        protected void Done() { Evt("done", Current, CurrentCorr); Current = null; PublishState(); }
        protected void Fail(string reason) { Evt("failed", Current, CurrentCorr, reason); Current = null; PublishState(); }

        protected void Evt(string ev, TCmd c, string corr, string reason = null)
        {
            var d = Describe(c);
            Mq.Send(EvtType, new DeviceEvt { @event = ev, cmd_id = d.cmdId, job_id = d.jobId, pallet = d.pallet, reason = reason },
                    EvtTopic, corr);
        }

        /// 전제 조건이 될 때까지 기다린다 (timeout 초 넘으면 false)
        protected IEnumerator WaitFor(System.Func<bool> cond, float timeout, System.Action<bool> result)
        {
            float t = 0f;
            while (!cond()) { if ((t += Time.deltaTime) > timeout) { result(false); yield break; } yield return null; }
            result(true);
        }
    }

    /// 리프트·셔틀이 팔레트를 주고받는 이송점·레인 (씬 쪽 재고 모델이 구현)
    public interface IRackModel
    {
        string BufferSlot(string buffer, int slot);                 // null = 빈 칸
        void SetBufferSlot(string buffer, int slot, string pallet);
        string Handoff(int rail, int level, bool inbound);          // 층별 입고/출고 이송점
        void SetHandoff(int rail, int level, bool inbound, string pallet);
        string LanePallet(string lane, int pos);
        void SetLanePallet(string lane, int pos, string pallet);
    }

    public class LiftMqttAgent : DeviceAgentBase<LiftCmd>
    {
        public string liftId = "L1";
        public float levelHeight = 1.5f, speed = 1.5f, overhead = 2f, transferSeconds = 5f;
        public Transform carriage;
        public MonoBehaviour rackModel;       // IRackModel 구현
        float _level; string _pallet;
        IRackModel Rack => (IRackModel)rackModel;
        int Rail => liftId[1] - '0';

        protected override string DeviceId => liftId;
        protected override string CmdTopic => WhTopics.LiftCmd(liftId);
        protected override string EvtTopic => WhTopics.LiftEvt(liftId);
        protected override string EvtType => "lift.evt";
        protected override (string, string, string, string) Describe(LiftCmd c) => (c.cmd_id, c.job_id, c.pallet, c.action);

        protected override IEnumerator Execute(LiftCmd c)
        {
            bool ok = true;
            string bufIn = liftId + "-IN", bufOut = liftId + "-OUT";
            if (c.action == "lift_in")
            {
                yield return MoveTo(0);
                yield return WaitFor(() => Rack.BufferSlot(bufIn, 1) == c.pallet, 600f, r => ok = r);
                if (!ok) { Fail($"{bufIn} 1번 칸에 {c.pallet} 없음"); yield break; }
                yield return new WaitForSeconds(transferSeconds);
                Rack.SetBufferSlot(bufIn, 1, null); _pallet = c.pallet;
                yield return MoveTo(c.level);
                yield return WaitFor(() => Rack.Handoff(Rail, c.level, true) == null, 600f, r => ok = r);
                if (!ok) { Fail("입고 이송점 사용 중"); yield break; }
                yield return new WaitForSeconds(transferSeconds);
                Rack.SetHandoff(Rail, c.level, true, c.pallet); _pallet = null;
            }
            else if (c.action == "lift_out")
            {
                yield return MoveTo(c.level);
                yield return WaitFor(() => Rack.Handoff(Rail, c.level, false) == c.pallet, 600f, r => ok = r);
                if (!ok) { Fail("출고 이송점에 팔레트 없음"); yield break; }
                yield return new WaitForSeconds(transferSeconds);
                Rack.SetHandoff(Rail, c.level, false, null); _pallet = c.pallet;
                yield return MoveTo(0);
                yield return WaitFor(() => Rack.BufferSlot(bufOut, 1) == null, 600f, r => ok = r);
                if (!ok) { Fail($"{bufOut} 1번 칸 사용 중"); yield break; }
                yield return new WaitForSeconds(transferSeconds);
                Rack.SetBufferSlot(bufOut, 1, c.pallet); _pallet = null;
            }
            Done();
        }

        IEnumerator MoveTo(int level)
        {
            float from = _level, dur = overhead + Mathf.Abs(level - from) * levelHeight / speed, t = 0f;
            while (t < dur)
            {
                t += Time.deltaTime;
                _level = Mathf.Lerp(from, level, t / dur);
                if (carriage) carriage.localPosition = new Vector3(0f, Mathf.Max(0, _level - 1) * levelHeight, 0f);
                yield return null;
            }
            _level = level;
            PublishState();
        }

        protected override void PublishState() =>
            Mq.Send("lift.state", new LiftState
            {
                mode = Current == null ? "idle" : "transferring", level = Mathf.Round(_level * 100f) / 100f,
                pallet = _pallet, cmd_id = Current?.cmd_id, queue = QueueCount,
            }, WhTopics.LiftState(liftId));
    }

    public class ShuttleMqttAgent : DeviceAgentBase<ShuttleCmd>
    {
        public string shuttleId = "MS-R1-LV01";
        public float motherSpeed = 2f, motherOverhead = 3f, satSpeed = 1f, satTransfer = 4f, slotPitch = 1.3f;
        public float liftY = 20.4f;                  // 이송점 (도면 y)
        public Transform mother, satellite;
        public MonoBehaviour rackModel;
        float _y; string _pallet, _satLane; float? _satPos;
        IRackModel Rack => (IRackModel)rackModel;
        int Rail => shuttleId[4] - '0';
        int Level => int.Parse(shuttleId.Substring(8, 2));

        protected override string DeviceId => shuttleId;
        protected override string CmdTopic => WhTopics.ShuttleCmd(shuttleId);
        protected override string EvtTopic => WhTopics.ShuttleEvt(shuttleId);
        protected override string EvtType => "shuttle.evt";
        protected override (string, string, string, string) Describe(ShuttleCmd c) => (c.cmd_id, c.job_id, c.pallet, c.action);

        protected override void Start() { _y = liftY; base.Start(); }

        protected override IEnumerator Execute(ShuttleCmd c)
        {
            bool ok = true;
            float laneY = LaneRowY(c.lane);
            int pos = c.pos ?? 1;
            if (c.action == "store")
            {
                yield return WaitFor(() => Rack.Handoff(Rail, Level, true) == c.pallet, 600f, r => ok = r);
                if (!ok) { Fail("입고 이송점에 팔레트 없음"); yield break; }
                yield return MoveMother(liftY, satTransfer);
                Rack.SetHandoff(Rail, Level, true, null); _pallet = c.pallet;
                yield return MoveMother(laneY, 0f);
                yield return Satellite(c.lane, pos);
                if (Rack.LanePallet(c.lane, pos) != null) { Fail($"{c.lane} 위치 {pos} 이미 차 있음"); yield break; }
                Rack.SetLanePallet(c.lane, pos, c.pallet); _pallet = null;
                yield return Satellite(null, pos);
            }
            else if (c.action == "retrieve")
            {
                yield return MoveMother(laneY, 0f);
                yield return Satellite(c.lane, pos);
                if (Rack.LanePallet(c.lane, pos) == null) { Fail($"{c.lane} 위치 {pos} 비어 있음"); yield break; }
                Rack.SetLanePallet(c.lane, pos, null); _pallet = c.pallet;
                yield return Satellite(null, pos);
                yield return MoveMother(liftY, 0f);
                yield return WaitFor(() => Rack.Handoff(Rail, Level, false) == null, 600f, r => ok = r);
                if (!ok) { Fail("출고 이송점 사용 중"); yield break; }
                yield return new WaitForSeconds(satTransfer);
                Rack.SetHandoff(Rail, Level, false, c.pallet); _pallet = null;
            }
            else { Fail($"{c.action} 미구현"); yield break; }
            Done();
        }

        /// 레인 ID 의 줄 번호 → 도면 y (unity/d4_layout.json 의 lanes[].center_z 와 같은 식)
        static float LaneRowY(string lane)
        {
            if (string.IsNullOrEmpty(lane)) return 20.4f;
            int row = int.Parse(lane.Substring(lane.Length - 2));
            return 31.4f + (row - 0.5f) * 1.4f;
        }

        IEnumerator MoveMother(float y, float extra)
        {
            float from = _y, dur = motherOverhead + Mathf.Abs(y - from) / motherSpeed + extra, t = 0f;
            while (t < dur)
            {
                t += Time.deltaTime;
                _y = Mathf.Lerp(from, y, Mathf.Clamp01(t / dur));
                if (mother) { var p = mother.position; mother.position = new Vector3(p.x, p.y, _y); }
                yield return null;
            }
            PublishState();
        }

        IEnumerator Satellite(string lane, int pos)
        {
            _satLane = lane; _satPos = lane == null ? (float?)null : pos;
            PublishState();
            yield return new WaitForSeconds(satTransfer + (pos - 0.5f) * slotPitch / satSpeed);
        }

        protected override void PublishState() =>
            Mq.Send("shuttle.state", new ShuttleState
            {
                mode = Current == null ? "idle" : _satLane != null ? "in_lane" : "moving", y = Mathf.Round(_y * 100f) / 100f,
                satellite = new Satellite { where = _satLane != null ? "in_lane" : "docked", lane = _satLane, pos = _satPos },
                pallet = _pallet, cmd_id = Current?.cmd_id, queue = QueueCount,
            }, WhTopics.ShuttleState(shuttleId));
    }

    /// 버퍼·도크·충전기처럼 상태만 내보내는 설비: 씬 스크립트가 값이 바뀔 때 호출한다.
    public static class FacilityMqtt
    {
        public static void Buffer(string bufferId, string[] slots, bool blocked = false) =>
            WhMqttClient.Instance.Send("buffer.state", new BufferState { slots = slots, blocked = blocked },
                                       WhTopics.BufferState(bufferId));

        public static void Dock(string dockId, DockState s) =>
            WhMqttClient.Instance.Send("dock.state", s, WhTopics.DockState(dockId));

        public static void DockEvent(string dockId, DockEvt e) =>
            WhMqttClient.Instance.Send("dock.evt", e, WhTopics.DockEvt(dockId));

        public static void Scale(string scaleId, ScaleReading r) =>
            WhMqttClient.Instance.Send("scale.reading", r, WhTopics.ScaleReading(scaleId));

        public static void Charger(string chargerId, string amr) =>
            WhMqttClient.Instance.Send("charger.state",
                new ChargerState { occupied_by = amr, charging = amr != null, power_kw = amr != null ? 3.0 : 0.0 },
                WhTopics.ChargerState(chargerId));
    }
}
