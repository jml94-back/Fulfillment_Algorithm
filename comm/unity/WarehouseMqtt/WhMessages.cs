// 메시지 본문(data) DTO. 필드명 = JSON 키 (schemas/*.schema.json 과 같음). Newtonsoft.Json 사용.
// Unity 패키지: com.unity.nuget.newtonsoft-json (Package Manager → Add package by name)
using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Warehouse.Mqtt
{
    /// 봉투: {"v":1,"id":..,"ts":..,"t":..,"src":..,"type":..,"corr":..,"data":{..}}
    public class Envelope
    {
        public int v = 1;
        public string id;
        public double ts;
        public double? t;
        public string src;
        public string type;
        public string corr;
        public JObject data;

        public T Data<T>() => data.ToObject<T>();

        public static Envelope Make(string type, object body, string src, double? simT, string corr = null)
        {
            return new Envelope
            {
                id = Guid.NewGuid().ToString("N").Substring(0, 16),
                ts = Math.Round(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0, 3),
                t = simT.HasValue ? Math.Round(simT.Value, 3) : (double?)null,
                src = src, type = type, corr = corr,
                data = JObject.FromObject(body, Json.Serializer),
            };
        }
    }

    public static class Json
    {
        public static readonly JsonSerializerSettings Settings = new JsonSerializerSettings
        {
            NullValueHandling = NullValueHandling.Include,     // 스키마는 null 필드도 요구한다
            FloatFormatHandling = FloatFormatHandling.DefaultValue,
        };
        public static readonly JsonSerializer Serializer = JsonSerializer.Create(Settings);
        public static string Encode(Envelope e) => JsonConvert.SerializeObject(e, Formatting.None, Settings);
        public static Envelope Decode(string s) => JsonConvert.DeserializeObject<Envelope>(s, Settings);
    }

    // ── 공통 ─────────────────────────────────────────────────────────────
    public class Online { public bool online; public string role; public string version; }
    public class SimClock { public double t; public double scale; public bool paused; }
    public class SimControl { public string action; public double? scale; public int? seed; }

    // ── AMR ───────────────────────────────────────────────────────────────
    public class AmrPose
    {
        public double x, y, theta, v, w, battery;
        public double? load_kg;
        public string node;
    }

    public class AmrScan2d
    {
        public double angle_min;
        public double angle_inc;
        public double range_max;
        public float[] ranges;
    }

    public class AmrState
    {
        public string mode;          // idle moving picking dropping charging waiting paused error offline
        public string drive_mode;    // scripted | rl
        public double battery;
        public string pallet;
        public string mission_id;
        public int? step;
        public string node;
        public string error;
    }

    public class AmrEvt
    {
        public string @event;        // mission_accepted mission_rejected step_started step_done mission_done ...
        public string mission_id;
        public int? seq;
        public string pallet;
        public string reason;
    }

    public class Waypoint { public string node; public double x, y; public double? t_start, t_end; }

    public class MissionStep
    {
        public int seq;
        public string action;        // move pick drop charge wait
        public string stop;
        public string pallet;
        public List<Waypoint> path = new List<Waypoint>();
        public double? theta;
        public double? duration;
        public double? deadline;
    }

    public class AmrMission
    {
        public string mission_id;
        public string job_id;
        public int priority;
        public List<MissionStep> steps;
    }

    public class AmrVel { public double v, w; public long seq; public double ttl; }

    public class AmrControl { public string action; public string drive_mode; public string charger; }

    // ── 리프트 · 셔틀 · 버퍼 ───────────────────────────────────────────────
    public class LiftCmd { public string cmd_id, job_id, action, pallet; public int level; }

    public class LiftState
    {
        public string mode; public double level; public string pallet; public string cmd_id; public int queue;
        public string error;
    }

    public class ShuttleCmd
    {
        public string cmd_id, job_id, action, pallet, lane, to_lane;
        public int? pos, to_pos;
    }

    public class Satellite { public string where; public string lane; public double? pos; }

    public class ShuttleState
    {
        public string mode; public double y; public Satellite satellite = new Satellite();
        public string pallet; public string cmd_id; public int queue; public double? battery; public string error;
    }

    public class DeviceEvt { public string @event, cmd_id, job_id, pallet, reason; }

    public class BufferState { public string[] slots = new string[5]; public bool blocked; }

    // ── 도크 · 계량 · 충전 ────────────────────────────────────────────────
    public class DockState
    {
        public string status, truck, order_id; public int pallets_total, pallets_done;
    }

    public class DockEvt { public string @event, truck, order_id, pallet, station; }

    public class ScaleReading { public string pallet, sku; public double weight_kg; public double? declared_kg; public bool stable; }

    public class ChargerState { public string occupied_by; public bool charging; public double power_kw; }

    // ── 스케줄러 → 시각화용 ───────────────────────────────────────────────
    public class InventorySnapshot
    {
        public List<JArray> lanes;    // [레인 ID, SKU, 수량]
        public int pallets, capacity, held;
    }

    public class Reservation { public string amr, mission_id, node; public double t_start, t_end; }
    public class Reservations { public double horizon; public List<Reservation> items; }
}
