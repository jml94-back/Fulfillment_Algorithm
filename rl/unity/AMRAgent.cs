// AMR 에이전트 뼈대 (ML-Agents 2.x / Unity 2022.3+). Python 프로토타입 rl/amr_env.py 와 같은 관측·행동·보상(최종 4차 값).
// Unity 담당 팀원이 창고 씬에 붙여 채우는 출발점이다. 물리는 Rigidbody 대신 운동학(차동 구동)으로 단순화했다.
//
// 붙이는 컴포넌트: AMRAgent, Lidar2DSensorComponent, BehaviorParameters(이름 "AMR", 연속 행동 2, 벡터 관측 27),
//                  DecisionRequester(DecisionPeriod 5, Academy 고정 스텝 0.02 s → 0.1 s 마다 결정)
// 경유점은 MissionSource 가 준다: 학습 때는 씬 안 플래너(경로 A*), 실행 때는 MQTT wh/v1/amr/<id>/cmd/mission.
using System.Collections.Generic;
using System.Linq;
using Unity.MLAgents;
using Unity.MLAgents.Actuators;
using Unity.MLAgents.Sensors;
using UnityEngine;

public interface IMissionSource
{
    // 다음 구간(빈 차로 집으러 가기 / 싣고 내려놓으러 가기)의 경유점과 적재 무게(kg)
    (List<Vector3> waypoints, float payloadKg) NextLeg(AMRAgent amr);
}

public class AMRAgent : Agent
{
    [Header("차량 한계 (빈 차 기준, 무게 1500 kg 에서 v -0.5, 가속 -0.4)")]
    public float vMax = 1.5f, vBack = 0.3f, wMax = 1.2f, acc = 1.0f, brake = 1.5f, angAcc = 3f;
    public float robotRadius = 0.7f;

    [Header("보상 (rl/amr_env.py RewardCfg 와 동일)")]
    public float rProgress = 1f, rWaypoint = 0.5f, rArrive = 10f, rCollision = -30f, rNear = -0.2f,
                 rSafety = -0.3f, rPassClose = -0.3f, rIdle = -0.02f,
                 rWorkerSpeed = -0.3f, rTime = -0.01f, rSmooth = -0.02f, rTimeout = -5f;

    public FleetArea area;                 // 같은 창고의 AMR 4대·작업자·SimpleMultiAgentGroup 을 가진 관리자
    public Lidar2DSensorComponent lidar;

    float v, w, payloadKg, dPrev;
    Vector2 prevAction;
    List<Vector3> wps = new();
    int wpIdx, sinceWp;

    public float Speed => v;
    public float PayloadKg => payloadKg;
    public Vector3 CurrentWaypoint => wps.Count > 0 ? wps[wpIdx] : transform.position;

    public override void OnEpisodeBegin()
    {
        // 에피소드 = 경로 구간 1개. 충돌로 끝났으면 FleetArea 가 빈 자리로 옮겨 둔다.
        v = w = 0f;
        prevAction = Vector2.zero;
        (wps, payloadKg) = area.Missions.NextLeg(this);
        wpIdx = 0;
        sinceWp = 0;
        dPrev = Flat(CurrentWaypoint - transform.position).magnitude;
    }

    public override void CollectObservations(VectorSensor s)
    {
        // LiDAR 72 x 2 프레임은 Lidar2DSensorComponent 가 따로 넣는다. 여기는 27개.
        AddGoal(s, wps[wpIdx]);                                          // 3
        AddGoal(s, wps[Mathf.Min(wpIdx + 1, wps.Count - 1)]);            // 3
        s.AddObservation(wpIdx == wps.Count - 1 ? 1f : 0f);              // 1
        s.AddObservation(v / vMax);                                      // 3
        s.AddObservation(w / wMax);
        s.AddObservation(payloadKg / 1500f);
        s.AddObservation(prevAction);                                    // 2
        // 가까운 AMR 3대 (MQTT pose 공유를 가정): 로봇 좌표계 상대 위치/10, 상대 속도/1.5, 존재 플래그  // 15
        var me = Flat(transform.position);
        var near = area.Agents.Where(a => a != this && a.gameObject.activeSelf)
                              .Select(a => (a, d: (Flat(a.transform.position) - me).magnitude))
                              .Where(t => t.d < 10f).OrderBy(t => t.d).Take(3).ToList();
        for (int k = 0; k < 3; k++)
        {
            if (k < near.Count)
            {
                var a = near[k].a;
                Vector3 rel = transform.InverseTransformDirection(a.transform.position - transform.position);
                Vector3 rv = transform.InverseTransformDirection(a.transform.forward * a.Speed - transform.forward * v);
                s.AddObservation(new Vector2(rel.z, -rel.x) / 10f);       // (앞, 왼쪽)
                s.AddObservation(new Vector2(rv.z, -rv.x) / vMax);
                s.AddObservation(1f);
            }
            else
            {
                s.AddObservation(Vector2.zero); s.AddObservation(Vector2.zero); s.AddObservation(0f);
            }
        }
    }

    void AddGoal(VectorSensor s, Vector3 p)
    {
        Vector3 rel = transform.InverseTransformDirection(p - transform.position);
        float d = new Vector2(rel.x, rel.z).magnitude;
        float bearing = Mathf.Atan2(-rel.x, rel.z);                     // 왼쪽(+) 기준, Python 과 같은 부호
        s.AddObservation(Mathf.Min(d, 10f) / 10f);
        s.AddObservation(Mathf.Cos(bearing));
        s.AddObservation(Mathf.Sin(bearing));
    }

    public override void OnActionReceived(ActionBuffers actions)
    {
        var a = actions.ContinuousActions;
        float a0 = Mathf.Clamp(a[0], -1f, 1f), a1 = Mathf.Clamp(a[1], -1f, 1f);
        float dt = Time.fixedDeltaTime * GetComponent<DecisionRequester>().DecisionPeriod;
        float lf = payloadKg / 1500f;
        float vm = vMax - 0.5f * lf, ac = acc - 0.4f * lf, br = brake - 0.5f * lf;
        float vt = a0 >= 0 ? a0 * vm : a0 * vBack, wt = a1 * wMax;
        float lim = (Mathf.Abs(vt) > Mathf.Abs(v) ? ac : br) * dt;
        v += Mathf.Clamp(vt - v, -lim, lim);
        w += Mathf.Clamp(wt - w, -angAcc * dt, angAcc * dt);
        transform.Rotate(0f, -w * Mathf.Rad2Deg * dt, 0f);               // 반시계(+w) = 위에서 볼 때 왼쪽
        transform.position += transform.forward * v * dt;

        var act = new Vector2(a0, a1);
        AddReward(rTime + rSmooth * (act - prevAction).sqrMagnitude);
        prevAction = act;

        if (area.Collides(this)) { AddReward(rCollision); area.OnCollision(this); EndEpisode(); return; }

        float d = Flat(wps[wpIdx] - transform.position).magnitude;
        AddReward(rProgress * (dPrev - d));
        sinceWp++;
        bool final = wpIdx == wps.Count - 1;
        if (final && d < 0.5f && Mathf.Abs(v) < 0.3f)
        {
            AddReward(rArrive);
            area.Group.AddGroupReward(0.1f);       // 처리량 보너스 (그룹 공동)
            EndEpisode();                          // 다음 구간은 OnEpisodeBegin 에서
            return;
        }
        if (!final && d < 1.2f) { AddReward(rWaypoint); wpIdx++; sinceWp = 0; d = Flat(wps[wpIdx] - transform.position).magnitude; }
        if (sinceWp > 400) { AddReward(rTimeout); EndEpisode(); return; }
        dPrev = d;

        var scan = lidar.LastRangesMeters;
        float m = scan?.Min() ?? 10f;
        float front = 10f;                                         // 정면 ±30° (빔 0~6, 66~71)
        if (scan != null) for (int k = -6; k <= 6; k++) front = Mathf.Min(front, scan[(k + scan.Length) % scan.Length]);
        if (v > 0.05f)                                             // 속도 의존 안전 영역
        {
            float field = v * v / (2f * br) + 0.3f, gap = front - robotRadius;
            if (gap < field) AddReward(rSafety * (field - gap));
        }
        if (Mathf.Abs(v) < 0.1f && front > 2.5f) AddReward(rIdle);   // 막히지 않았는데 멈춤
        if (m < 1.2f) AddReward(rNear * Mathf.Min(1f, (1.2f - m) / 0.5f));
        foreach (var o in area.Agents)                             // 마주 접근
        {
            if (o == this || !o.gameObject.activeSelf) continue;
            Vector3 dp = Flat(o.transform.position - transform.position);
            float dd = dp.magnitude;
            if (dd >= 2.2f || dd < 1e-4f) continue;
            Vector3 dv = o.transform.forward * o.Speed - transform.forward * v;
            float closing = -Vector3.Dot(Flat(dv), dp) / dd;
            if (closing > 0f) AddReward(rPassClose * (2.2f - dd) * Mathf.Min(closing, 3f));
        }
        if (area.NearestWorkerDistance(transform.position) < 2.5f && Mathf.Abs(v) > 0.5f)
            AddReward(rWorkerSpeed * (Mathf.Abs(v) - 0.5f));
    }

    public override void Heuristic(in ActionBuffers actionsOut)
    {
        var a = actionsOut.ContinuousActions;            // 키보드로 몰아보기
        a[0] = Input.GetAxis("Vertical");
        a[1] = -Input.GetAxis("Horizontal");
    }

    static Vector3 Flat(Vector3 p) => new(p.x, 0f, p.z);
}

// FleetArea (별도 파일로 작성): Agents 목록, Group(SimpleMultiAgentGroup) 등록, Missions(IMissionSource),
// Collides(): 다른 AMR(거리 < 1.4 m)·작업자(< 1.0 m)·고정물(OverlapSphere r 0.7) 검사,
// OnCollision(): 빈 통로 위치로 순간 이동, NearestWorkerDistance(), 단계(stage) 값에 따라 AMR 활성 대수·작업자·임시 팔레트 배치,
// 1500 결정 스텝마다 Group.GroupEpisodeInterrupted() 로 그룹 에피소드를 끊는다.
