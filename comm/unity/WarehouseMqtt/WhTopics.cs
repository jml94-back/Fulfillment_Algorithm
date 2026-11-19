// 토픽 문자열과 토픽별 QoS·retain·만료. Python whmqtt/topics.py 와 같은 내용 (spec/topics.json 참고).
using System.Collections.Generic;

namespace Warehouse.Mqtt
{
    public readonly struct TopicSpec
    {
        public readonly int Qos;
        public readonly bool Retain;
        public readonly uint ExpirySec;   // 0 = 만료 없음
        public TopicSpec(int qos, bool retain, uint expiry = 0) { Qos = qos; Retain = retain; ExpirySec = expiry; }
    }

    public static class WhTopics
    {
        public const string Root = "wh/v1";

        // type(key) → 설정. Unity 가 발행하는 것 + 구독하는 명령.
        public static readonly Dictionary<string, TopicSpec> Spec = new Dictionary<string, TopicSpec>
        {
            { "sys.online",      new TopicSpec(1, true) },
            { "sim.clock",       new TopicSpec(0, true) },
            { "amr.pose",        new TopicSpec(0, false, 1) },
            { "amr.scan2d",      new TopicSpec(0, false, 1) },
            { "amr.state",       new TopicSpec(1, true) },
            { "amr.evt",         new TopicSpec(1, false) },
            { "rack.evt",        new TopicSpec(1, false) },
            { "rack.state",      new TopicSpec(1, true) },
            { "rack.inventory",  new TopicSpec(1, true) },
            { "lift.state",      new TopicSpec(1, true) },
            { "buffer.state",    new TopicSpec(1, true) },
            { "shuttle.state",   new TopicSpec(1, true) },
            { "dock.state",      new TopicSpec(1, true) },
            { "dock.evt",        new TopicSpec(1, false) },
            { "scale.reading",   new TopicSpec(1, false) },
            { "charger.state",   new TopicSpec(1, true) },
        };

        public static string Online(string clientId) => $"{Root}/sys/{clientId}/online";
        public const string SimClock = Root + "/sim/clock";
        public const string SimControl = Root + "/sim/control";

        public static string AmrPose(string amr) => $"{Root}/amr/{amr}/pose";
        public static string AmrScan2d(string amr) => $"{Root}/amr/{amr}/scan2d";
        public static string AmrState(string amr) => $"{Root}/amr/{amr}/state";
        public static string AmrEvt(string amr) => $"{Root}/amr/{amr}/evt";
        public static string AmrMission(string amr) => $"{Root}/amr/{amr}/cmd/mission";
        public static string AmrVel(string amr) => $"{Root}/amr/{amr}/cmd/vel";
        public static string AmrControl(string amr) => $"{Root}/amr/{amr}/cmd/control";

        // 랙 = 통로(R1~R3) 1개 = ShuttleRackSystem 1개. 명령은 rack/{rail}/cmd 하나뿐이고
        // 리프트·모셔틀은 랙이 직접 움직이므로 상태만 내보낸다.
        public static string RackCmd(string rail) => $"{Root}/rack/{rail}/cmd";
        public static string RackEvt(string rail) => $"{Root}/rack/{rail}/evt";
        public static string RackState(string rail) => $"{Root}/rack/{rail}/state";
        public static string RackInventory(string rail) => $"{Root}/rack/{rail}/inventory";
        public static string LiftState(string lift) => $"{Root}/lift/{lift}/state";
        public static string BufferState(string buf) => $"{Root}/buffer/{buf}/state";       // R1-IN, R1-OUT …
        public static string ShuttleState(string sh) => $"{Root}/shuttle/{sh}/state";      // R1-Shuttle_L01 …

        public static string DockState(string dock) => $"{Root}/dock/{dock}/state";
        public static string DockEvt(string dock) => $"{Root}/dock/{dock}/evt";
        public static string ScaleReading(string scale) => $"{Root}/scale/{scale}/reading";
        public static string ChargerState(string ch) => $"{Root}/charger/{ch}/state";

        public const string WmsOrder = Root + "/wms/order";
        public const string WmsJob = Root + "/wms/job";
        public const string WmsInventory = Root + "/wms/inventory/evt";
        public const string WmsSnapshot = Root + "/wms/inventory/snapshot";
        public const string Reservations = Root + "/traffic/reservations";
    }
}
