// 랙(통로)·리프트·셔틀·버퍼·도크·충전기 ↔ MQTT.
//
// 랙 (R1~R3, ShuttleRackSystem 1개)  구독 rack/{rail}/cmd
//                                    발행 rack/{rail}/evt, rack/{rail}/state, rack/{rail}/inventory
//                                         lift/L{n}/state, shuttle/R{n}-Shuttle_L01~10/state, buffer/R{n}-IN|OUT/state
// 도크 (IN1 … OUT6)                   발행 dock/{id}/state, dock/{id}/evt, (입고) scale/SC-INn/reading
// 충전기 (CHW1 …)                     발행 charger/{id}/state
//
// 레인(채널)·칸 선택과 리프트·모셔틀 제어는 ShuttleRackSystem 이 전부 한다 (Warehouse_System_Spec 5.3·7장).
// 스케줄러는 통로만 골라 store / retrieve 를 요청하고, 이 스크립트는 그 요청을 ShuttleRackSystem API 로 옮기고
// 결과(accepted/rejected/started/done)를 corr = 명령 봉투 id 로 돌려준다.
//
// ShuttleRackSystem 의 RackTask·PalletUnit 타입을 이 패키지가 직접 참조하지 않도록, 씬 쪽에서 IRackSystem 을
// 구현하는 얇은 연결 스크립트(예: ShuttleRackMqttGlue)를 하나 만들어 rackSystem 칸에 끼운다.
using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

namespace Warehouse.Mqtt
{
    /// 통로 1개 (= ShuttleRackSystem 1개) 를 MQTT 쪽에서 보는 창. task 는 RackTask 를 그대로 넘기면 된다.
    public interface IRackSystem
    {
        // 요청 (ShuttleRackSystem.RequestStore / RequestRetrieve). 실패하면 null.
        object RequestStore(string palletId);           // 입고 버퍼에 있는 팔레트
        object RequestRetrieve(string sku);
        string TaskChannel(object task);                 // 예약한 채널 ID (R1-E07-L03)
        int? TaskSlot(object task);                      // 실제 칸 번호 1~14 (01 = 통로 쪽 앞칸). 실행 전이면 null
        string TaskPallet(object task);                  // 팔레트 ID. 출고는 실행 전이면 null
        event Action<object> TaskStarted;
        event Action<object> TaskCompleted;

        // 상태 (ShuttleRackSystem 공개 속성)
        int StoredCount { get; }
        int Capacity { get; }
        int PendingTasks { get; }
        int TotalStored { get; }
        int TotalRetrieved { get; }
        float AvgCycleSeconds { get; }                   // 아직 없으면 음수
        bool InboundFree { get; }
        /// 채널마다 (채널 ID, SKU, 실제 수량, 가상 수량). SKU 가 배정된 채널만.
        IEnumerable<(string channel, string sku, int count, int virtualCount)> ChannelSummary();

        // 구동부 (모니터링 전용)
        (string status, float height, string pallet, string owner) Lift();
        /// 단(1~10)마다 모셔틀 상태. y = 도면 y(m). 새틀라이트가 레인 안이면 satChannel·satDepth.
        IEnumerable<(int level, bool busy, string status, float y, string satChannel, float? satDepth, string pallet)> Shuttles();
        /// 버퍼 5칸 (0 = IN1/OUT1 = 리프트 쪽). ShuttleRackSystem 이 inBuf/outBuf 를 공개하지 않으면 null.
        string[] InboundBuffer();
        string[] OutboundBuffer();
    }

    public class RackMqttBridge : MonoBehaviour
    {
        public string railId = "R1";                     // R1 ~ R3
        public MonoBehaviour rackSystem;                 // IRackSystem 구현
        public float statePeriod = 0.5f;                 // 상태 변경 확인 주기 (초)
        public float inventoryPeriod = 60f;              // rack/{rail}/inventory 주기 (초, 시뮬 시간)

        IRackSystem Rack => (IRackSystem)rackSystem;
        WhMqttClient _mq;
        readonly Dictionary<object, (RackCmd cmd, string corr)> _tasks = new Dictionary<object, (RackCmd, string)>();
        readonly Dictionary<string, string> _last = new Dictionary<string, string>();   // 토픽 → 마지막으로 보낸 본문
        float _nextState, _nextInv;
        bool _invDirty = true;

        string LiftId => "L" + railId.Substring(1);

        void Start()
        {
            _mq = WhMqttClient.Instance;
            Rack.TaskStarted += t => Evt("started", t);
            Rack.TaskCompleted += t => { Evt("done", t); _tasks.Remove(t); _invDirty = true; };
            _mq.On(WhTopics.RackCmd(railId), (env, _) => OnCmd(env.Data<RackCmd>(), env.id));
        }

        void OnCmd(RackCmd c, string corr)
        {
            object task = c.action == "store" ? Rack.RequestStore(c.pallet)
                        : c.action == "retrieve" ? Rack.RequestRetrieve(c.sku) : null;
            if (task == null)
            {
                string reason = c.action == "store" ? "만재 또는 입고 버퍼에 팔레트 없음" : c.action == "retrieve" ? "재고 없음" : "알 수 없는 action";
                Send(c, corr, "rejected", null, null, c.pallet, reason);
                return;
            }
            _tasks[task] = (c, corr);
            Send(c, corr, "accepted", Rack.TaskChannel(task), null, c.pallet ?? Rack.TaskPallet(task), null);
            _invDirty = true;
        }

        void Evt(string ev, object task)
        {
            if (!_tasks.TryGetValue(task, out var x)) return;     // 시연용 RackDemoDriver 등 MQTT 밖에서 만든 작업
            Send(x.cmd, x.corr, ev, Rack.TaskChannel(task), ev == "done" ? Rack.TaskSlot(task) : null,
                 Rack.TaskPallet(task) ?? x.cmd.pallet, null);
        }

        void Send(RackCmd c, string corr, string ev, string channel, int? slot, string pallet, string reason) =>
            _mq.Send("rack.evt", new RackEvt
            {
                @event = ev, cmd_id = c.cmd_id, job_id = c.job_id, action = c.action, pallet = pallet, sku = c.sku,
                channel = channel, slot = slot, reason = reason,
            }, WhTopics.RackEvt(railId), corr);

        void Update()
        {
            if (_mq == null) return;
            float now = Time.time;
            if (now >= _nextState) { _nextState = now + statePeriod; PublishStates(); }
            if (now >= _nextInv || (_invDirty && now >= _nextInv - inventoryPeriod + 5f))
            {
                _nextInv = now + inventoryPeriod; _invDirty = false; PublishInventory();
            }
        }

        /// 바뀐 것만 보낸다 (전부 retain 이라 늦게 붙은 구독자도 마지막 값을 받는다)
        void SendIfChanged(string type, object body, string topic)
        {
            string key = Json.Encode(new Envelope { type = type, data = Newtonsoft.Json.Linq.JObject.FromObject(body, Json.Serializer) });
            if (_last.TryGetValue(topic, out var prev) && prev == key) return;
            _last[topic] = key;
            _mq.Send(type, body, topic);
        }

        void PublishStates()
        {
            var r = Rack;
            SendIfChanged("rack.state", new RackState
            {
                stored = r.StoredCount, capacity = r.Capacity, pending = r.PendingTasks, inbound_free = r.InboundFree,
                total_stored = r.TotalStored, total_retrieved = r.TotalRetrieved,
                avg_cycle_s = r.AvgCycleSeconds < 0 ? (double?)null : Math.Round(r.AvgCycleSeconds, 1),
            }, WhTopics.RackState(railId));

            var lift = r.Lift();
            SendIfChanged("lift.state", new LiftState
            {
                status = lift.status, height = Math.Round(lift.height, 2), level = LevelAt(lift.height),
                pallet = lift.pallet, owner = lift.owner,
            }, WhTopics.LiftState(LiftId));

            foreach (var s in r.Shuttles())
                SendIfChanged("shuttle.state", new ShuttleState
                {
                    busy = s.busy, status = s.status, level = s.level, y = Math.Round(s.y, 2), pallet = s.pallet,
                    satellite = new Satellite
                    {
                        where = s.satChannel != null ? "in_lane" : "docked", channel = s.satChannel,
                        depth_m = s.satDepth.HasValue ? Math.Round(s.satDepth.Value, 2) : (double?)null,
                    },
                }, WhTopics.ShuttleState(WhIds.Shuttle(railId, s.level)));

            var inb = r.InboundBuffer();
            if (inb != null) SendIfChanged("buffer.state", new BufferState { slots = inb }, WhTopics.BufferState(railId + "-IN"));
            var outb = r.OutboundBuffer();
            if (outb != null) SendIfChanged("buffer.state", new BufferState { slots = outb }, WhTopics.BufferState(railId + "-OUT"));
        }

        void PublishInventory()
        {
            var inv = new RackInventory { stored = Rack.StoredCount, capacity = Rack.Capacity };
            foreach (var c in Rack.ChannelSummary()) inv.Add(c.channel, c.sku, c.count, c.virtualCount);
            _mq.Send("rack.inventory", inv, WhTopics.RackInventory(railId));
        }

        /// 리프트 높이가 어느 단 레일 높이(0.40 + 1.45(n-1))와 같으면 그 단(1~10), 아니면 null
        static int? LevelAt(float h)
        {
            float n = (h - WhIds.RailTop0) / WhIds.LevelHeight;
            int k = Mathf.RoundToInt(n);
            return Mathf.Abs(n - k) * WhIds.LevelHeight < 0.02f && k >= 0 && k < 10 ? k + 1 : (int?)null;
        }
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
