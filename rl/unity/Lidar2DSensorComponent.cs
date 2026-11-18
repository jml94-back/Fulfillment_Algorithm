// 2D LiDAR 센서 (ML-Agents 커스텀 센서) — Python 프로토타입(rl/amr_env.py)·MQTT scan2d 와 같은 형식.
// 72빔, 360°, 0번 = 로봇 정면, 반시계(위에서 볼 때 왼쪽으로 증가), 최대 10 m, 관측값 = 거리 / 10 (0~1).
//
// RayPerceptionSensorComponent3D 대신 쓰는 이유: RayPerception 은 "맞은 물체의 태그" 원-핫을 같이 넣는데,
// 실제 LiDAR 는 사람인지 팔레트인지 모른다. 정책이 그 정보에 기대면 실물로 옮길 때 깨지므로 거리만 준다.
// 도메인 무작위화(잡음·빔 누락)도 여기서 넣는다.
using System.Collections.Generic;
using Unity.MLAgents;
using Unity.MLAgents.Sensors;
using UnityEngine;

public class Lidar2DSensorComponent : SensorComponent
{
    public int beams = 72;
    public float maxRange = 10f;
    public float mountHeight = 0.25f;          // 바닥에서 스캔 평면까지(m). 팔레트 다리·사람 다리 높이
    public LayerMask obstacleMask = ~0;
    public int stack = 2;                      // 현재 + 직전 프레임 (움직이는 물체 속도 단서)
    public bool drawGizmos = true;

    Lidar2DSensor m_Sensor;
    public float[] LastRangesMeters => m_Sensor?.Ranges;   // MQTT wh/v1/amr/<id>/scan2d 로 그대로 내보낼 값

    public override ISensor[] CreateSensors()
    {
        m_Sensor = new Lidar2DSensor(this);
        return new ISensor[] { stack > 1 ? new StackingSensor(m_Sensor, stack) : (ISensor)m_Sensor };
    }

    void OnDrawGizmosSelected()
    {
        if (!drawGizmos || m_Sensor?.Ranges == null) return;
        var o = transform.position + Vector3.up * mountHeight;
        for (int i = 0; i < beams; i++)
        {
            Gizmos.color = m_Sensor.Ranges[i] < maxRange ? Color.red : Color.green;
            Gizmos.DrawLine(o, o + m_Sensor.Dir(i) * m_Sensor.Ranges[i]);
        }
    }

    class Lidar2DSensor : ISensor
    {
        readonly Lidar2DSensorComponent c;
        public float[] Ranges;
        readonly float[] m_Obs;

        public Lidar2DSensor(Lidar2DSensorComponent comp)
        {
            c = comp;
            Ranges = new float[c.beams];
            m_Obs = new float[c.beams];
        }

        public Vector3 Dir(int i)
        {
            // Unity 는 왼손 좌표계(y 위). 위에서 볼 때 반시계 = y 축 기준 음의 회전
            float deg = -360f * i / c.beams;
            return Quaternion.AngleAxis(deg, Vector3.up) * c.transform.forward;
        }

        public ObservationSpec GetObservationSpec() => ObservationSpec.Vector(c.beams);

        public void Update()
        {
            var env = Academy.Instance.EnvironmentParameters;
            float noise = env.GetWithDefault("lidar_noise_std", 0f);
            float drop = env.GetWithDefault("lidar_dropout", 0f);
            var o = c.transform.position + Vector3.up * c.mountHeight;
            for (int i = 0; i < c.beams; i++)
            {
                float d = c.maxRange;
                if (Physics.Raycast(o, Dir(i), out RaycastHit hit, c.maxRange, c.obstacleMask, QueryTriggerInteraction.Ignore))
                    d = hit.distance;
                if (noise > 0f) d += noise * Gaussian();
                if (drop > 0f && Random.value < drop) d = c.maxRange;      // 반사 실패 = 최대 거리
                Ranges[i] = Mathf.Clamp(d, 0f, c.maxRange);
                m_Obs[i] = Ranges[i] / c.maxRange;
            }
        }

        public int Write(ObservationWriter writer)
        {
            writer.AddList(m_Obs);
            return c.beams;
        }

        static float Gaussian()
        {
            float u1 = 1f - Random.value, u2 = Random.value;
            return Mathf.Sqrt(-2f * Mathf.Log(u1)) * Mathf.Cos(2f * Mathf.PI * u2);
        }

        public byte[] GetCompressedObservation() => null;
        public void Reset() { }
        public CompressionSpec GetCompressionSpec() => CompressionSpec.Default();
        public string GetName() => "Lidar2D";
    }
}
