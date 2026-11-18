// C# DTO → JSON 이 Python 스키마를 통과하는지, MQTTnet 으로 실제 브로커와 주고받는지 확인
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using MQTTnet;
using MQTTnet.Client;
using MQTTnet.Formatter;
using MQTTnet.Protocol;
using Warehouse.Mqtt;

class P
{
    static async Task<int> Main(string[] a)
    {
        var msgs = new List<(string type, object body)>
        {
            ("sys.online", new Online { online = true, role = "unity", version = "1.0.0" }),
            ("sim.clock", new SimClock { t = 12.5, scale = 1, paused = false }),
            ("amr.pose", new AmrPose { x = 1.5, y = 23.6, theta = 3.14, v = 0, w = 0, battery = 88.2, load_kg = null, node = "n_CHW1" }),
            ("amr.scan2d", new AmrScan2d { angle_min = 0, angle_inc = 0.0872665, range_max = 10, ranges = new float[72] }),
            ("amr.state", new AmrState { mode = "idle", drive_mode = "scripted", battery = 88.2 }),
            ("amr.evt", new AmrEvt { @event = "mission_done", mission_id = "M00001" }),
            ("lift.state", new LiftState { mode = "idle", level = 0, queue = 0 }),
            ("lift.evt", new DeviceEvt { @event = "done", cmd_id = "C00001", job_id = "J1", pallet = "P00000001" }),
            ("shuttle.state", new ShuttleState { mode = "idle", y = 20.4, satellite = new Satellite { where = "docked" }, queue = 0 }),
            ("shuttle.evt", new DeviceEvt { @event = "accepted", cmd_id = "C00002" }),
            ("buffer.state", new BufferState { slots = new string[] { "P00000001", null, null, null, null } }),
            ("dock.state", new DockState { status = "unloading", truck = "TRK-1", order_id = "ASN-1", pallets_total = 4, pallets_done = 1 }),
            ("dock.evt", new DockEvt { @event = "pallet_unloaded", truck = "TRK-1", order_id = "ASN-1", pallet = "P00000001", station = "SC-IN2" }),
            ("scale.reading", new ScaleReading { pallet = "P00000001", sku = "SKU101", weight_kg = 651.2, declared_kg = 650, stable = true }),
            ("charger.state", new ChargerState { occupied_by = "AMR01", charging = true, power_kw = 3 }),
        };
        using (var f = new StreamWriter(a[0]))
            foreach (var (t, b) in msgs) f.WriteLine(Json.Encode(Envelope.Make(t, b, "unity-sim", 12.5)));

        // 실제 브로커: Python 쪽이 보낸 미션을 받아 역직렬화
        var c = new MqttFactory().CreateMqttClient();
        var got = new TaskCompletionSource<AmrMission>();
        c.ApplicationMessageReceivedAsync += e =>
        {
            var s = e.ApplicationMessage.PayloadSegment;
            var env = Json.Decode(Encoding.UTF8.GetString(s.Array, s.Offset, s.Count));
            if (env.type == "amr.cmd.mission") got.TrySetResult(env.Data<AmrMission>());
            return Task.CompletedTask;
        };
        await c.ConnectAsync(new MqttClientOptionsBuilder().WithTcpServer("127.0.0.1", int.Parse(a[1]))
            .WithClientId("cs-interop").WithProtocolVersion(MqttProtocolVersion.V500).Build());
        await c.SubscribeAsync(new MqttClientSubscribeOptionsBuilder().WithTopicFilter(x => x.WithTopic(WhTopics.AmrMission("AMR02")).WithAtLeastOnceQoS()).Build());
        Console.WriteLine("READY");
        var done = await Task.WhenAny(got.Task, Task.Delay(10000));
        if (done != got.Task) { Console.WriteLine("TIMEOUT"); return 2; }
        var m = got.Task.Result;
        Console.WriteLine($"MISSION {m.mission_id} steps={m.steps.Count} first={m.steps[0].action}:{m.steps[0].stop} wp={m.steps[0].path.Count} t_start={m.steps[0].path[1].t_start}");
        var ack = Json.Encode(Envelope.Make("amr.evt", new AmrEvt { @event = "mission_accepted", mission_id = m.mission_id }, "cs-interop", 1.0));
        await c.PublishAsync(new MqttApplicationMessageBuilder().WithTopic(WhTopics.AmrEvt("AMR02")).WithPayload(ack)
            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce).Build());
        await c.DisconnectAsync();
        return 0;
    }
}
