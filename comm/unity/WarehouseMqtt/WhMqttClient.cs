// Unity ↔ MQTT 브로커 연결 (MQTTnet 4.3.x, MQTT v5).
//
// 설치
//   1) MQTTnet 4.3.7 DLL (netstandard2.0) 을 Assets/Plugins 에 넣는다.
//      NuGetForUnity 를 쓰면 "MQTTnet" 4.3.7 검색 후 설치.
//   2) Package Manager → Add package by name → com.unity.nuget.newtonsoft-json
//   3) 빈 GameObject 에 이 컴포넌트를 붙이고 Host / 사용자명(unity) / 비밀번호를 넣는다.
//
// 하는 일
//   - 접속, Last Will(sys/{clientId}/online = false), 끊기면 2초 간격으로 재접속하고 구독을 다시 건다.
//   - 받은 메시지는 네트워크 스레드에서 큐에 넣고 Update() 에서 메인 스레드로 꺼내 핸들러를 부른다
//     (Unity API 는 메인 스레드에서만 써야 하므로).
//   - 1초마다 sim/clock 발행. 시뮬레이션 시각 = 씬 시작 후 Time.time (Time.timeScale 이 배속).
//   - Send(type, body, topic) 은 WhTopics.Spec 의 QoS·retain·만료로 봉투를 만들어 발행한다.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MQTTnet;
using MQTTnet.Client;
using MQTTnet.Formatter;
using MQTTnet.Protocol;
using UnityEngine;

namespace Warehouse.Mqtt
{
    public class WhMqttClient : MonoBehaviour
    {
        public static WhMqttClient Instance { get; private set; }

        [Header("브로커 (Mac mini)")]
        public string host = "127.0.0.1";
        public int port = 1883;
        public string username = "unity";
        public string password = "";
        public string clientId = "unity-sim";

        [Header("시뮬레이션 시계")]
        public bool publishClock = true;

        public bool IsConnected => _client != null && _client.IsConnected;
        /// 시뮬레이션 시각(초). Update 에서 갱신한 값이라 다른 스레드에서 읽어도 안전하다.
        public double SimTime => Volatile.Read(ref _simNow);

        IMqttClient _client;
        MqttClientOptions _options;
        readonly ConcurrentQueue<(string topic, string payload)> _inbox = new ConcurrentQueue<(string, string)>();
        readonly Dictionary<string, List<Action<Envelope, string[]>>> _handlers =
            new Dictionary<string, List<Action<Envelope, string[]>>>();
        readonly HashSet<string> _seen = new HashSet<string>();
        readonly Queue<string> _seenOrder = new Queue<string>();
        CancellationTokenSource _cts;
        double _t0, _simNow;
        float _nextClock;

        void Awake()
        {
            if (Instance != null && Instance != this) { Destroy(gameObject); return; }
            Instance = this;
            _t0 = Time.timeAsDouble;
        }

        void OnEnable()
        {
            _cts = new CancellationTokenSource();
            var factory = new MqttFactory();
            _client = factory.CreateMqttClient();
            var will = Json.Encode(Envelope.Make("sys.online",
                new Online { online = false, role = "unity", version = "1.0.0" }, clientId, null));
            _options = new MqttClientOptionsBuilder()
                .WithTcpServer(host, port)
                .WithClientId(clientId)
                .WithCredentials(username, password)
                .WithProtocolVersion(MqttProtocolVersion.V500)
                .WithCleanSession(true)
                .WithKeepAlivePeriod(TimeSpan.FromSeconds(15))
                .WithWillTopic(WhTopics.Online(clientId))
                .WithWillPayload(Encoding.UTF8.GetBytes(will))
                .WithWillQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                .WithWillRetain(true)
                .Build();
            _client.ApplicationMessageReceivedAsync += e =>
            {
                var seg = e.ApplicationMessage.PayloadSegment;
                if (seg.Count > 0)
                    _inbox.Enqueue((e.ApplicationMessage.Topic, Encoding.UTF8.GetString(seg.Array, seg.Offset, seg.Count)));
                return Task.CompletedTask;
            };
            _client.ConnectedAsync += async e =>
            {
                string[] filters;
                lock (_handlers) filters = new List<string>(_handlers.Keys).ToArray();
                foreach (var filter in filters) await SubscribeRaw(filter);
                Send("sys.online", new Online { online = true, role = "unity", version = "1.0.0" },
                     WhTopics.Online(clientId));
                Debug.Log($"[MQTT] {host}:{port} 접속");
            };
            _ = ConnectLoop(_cts.Token);
        }

        async Task ConnectLoop(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    if (!_client.IsConnected) await _client.ConnectAsync(_options, ct);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[MQTT] 접속 실패, 2초 뒤 재시도: {ex.Message}");
                }
                try { await Task.Delay(2000, ct); } catch (TaskCanceledException) { }
            }
        }

        async void OnDisable()
        {
            if (_client == null) return;
            _cts.Cancel();
            if (_client.IsConnected)
            {
                await Publish(WhTopics.Online(clientId), Json.Encode(Envelope.Make("sys.online",
                    new Online { online = false, role = "unity", version = "1.0.0" }, clientId, SimTime)), 1, true, 0);
                await _client.DisconnectAsync();
            }
            _client.Dispose();
            _client = null;
        }

        void Update()
        {
            Volatile.Write(ref _simNow, Time.timeAsDouble - _t0);
            int budget = 500;   // 한 프레임에 처리할 최대 메시지 수
            while (budget-- > 0 && _inbox.TryDequeue(out var m)) Dispatch(m.topic, m.payload);

            if (publishClock && IsConnected && Time.unscaledTime >= _nextClock)
            {
                _nextClock = Time.unscaledTime + 1f;
                Send("sim.clock", new SimClock { t = SimTime, scale = Time.timeScale, paused = Time.timeScale == 0f },
                     WhTopics.SimClock);
            }
        }

        // ── 구독 ────────────────────────────────────────────────────────
        /// filter 예: "wh/v1/amr/+/cmd/mission". handler(env, topic 을 / 로 나눈 조각)
        public void On(string filter, Action<Envelope, string[]> handler)
        {
            bool isNew;
            lock (_handlers)
            {
                isNew = !_handlers.TryGetValue(filter, out var list);
                if (isNew) { list = new List<Action<Envelope, string[]>>(); _handlers[filter] = list; }
                list.Add(handler);
            }
            if (isNew && IsConnected) _ = SubscribeRaw(filter);
        }

        Task SubscribeRaw(string filter) =>
            _client.SubscribeAsync(new MqttClientSubscribeOptionsBuilder()
                .WithTopicFilter(f => f.WithTopic(filter).WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce))
                .Build());

        void Dispatch(string topic, string payload)
        {
            Envelope env;
            try { env = Json.Decode(payload); }
            catch (Exception ex) { Debug.LogWarning($"[MQTT] JSON 오류 {topic}: {ex.Message}"); return; }
            if (env == null || env.v != 1) return;
            if (_seen.Contains(env.id)) return;          // QoS 1 중복 제거
            _seen.Add(env.id); _seenOrder.Enqueue(env.id);
            if (_seenOrder.Count > 5000) _seen.Remove(_seenOrder.Dequeue());
            var parts = topic.Split('/');
            KeyValuePair<string, List<Action<Envelope, string[]>>>[] snapshot;
            lock (_handlers) snapshot = new List<KeyValuePair<string, List<Action<Envelope, string[]>>>>(_handlers).ToArray();
            foreach (var kv in snapshot)
            {
                if (!Matches(kv.Key, parts)) continue;
                foreach (var h in kv.Value)
                {
                    try { h(env, parts); }
                    catch (Exception ex) { Debug.LogException(ex); }
                }
            }
        }

        static bool Matches(string filter, string[] parts)
        {
            var f = filter.Split('/');
            for (int i = 0; i < f.Length; i++)
            {
                if (f[i] == "#") return true;
                if (i >= parts.Length) return false;
                if (f[i] != "+" && f[i] != parts[i]) return false;
            }
            return f.Length == parts.Length;
        }

        // ── 발행 ────────────────────────────────────────────────────────
        /// type 의 QoS·retain·만료(WhTopics.Spec)로 봉투를 만들어 발행. corr = 응답하는 명령의 id.
        public void Send(string type, object body, string topic, string corr = null)
        {
            if (!IsConnected) return;
            var spec = WhTopics.Spec.TryGetValue(type, out var s) ? s : new TopicSpec(1, false);
            var json = Json.Encode(Envelope.Make(type, body, clientId, SimTime, corr));
            _ = Publish(topic, json, spec.Qos, spec.Retain, spec.ExpirySec);
        }

        async Task Publish(string topic, string json, int qos, bool retain, uint expiry)
        {
            var b = new MqttApplicationMessageBuilder()
                .WithTopic(topic)
                .WithPayload(json)
                .WithQualityOfServiceLevel((MqttQualityOfServiceLevel)qos)
                .WithRetainFlag(retain)
                .WithContentType("application/json");
            if (expiry > 0) b = b.WithMessageExpiryInterval(expiry);
            try { await _client.PublishAsync(b.Build(), _cts.Token); }
            catch (Exception ex) { Debug.LogWarning($"[MQTT] 발행 실패 {topic}: {ex.Message}"); }
        }
    }
}
