// MQTT 좌표(도면: x 동, y 북, z 위, theta = 동쪽에서 반시계 rad) ↔ Unity(Y-up, 왼손, yaw = +Z 에서 시계 deg).
// 도면 원점 = Unity 월드 (-60.005, 1.20, -39.705) (Warehouse_System_Spec 1장, DockZoneBuilder 기준).
using UnityEngine;

namespace Warehouse.Mqtt
{
    public static class WhCoords
    {
        /// 도면 원점(서쪽 벽 외부면 · 바닥 윗면 · 남쪽 벽 안쪽면)의 Unity 월드 위치 (= DockZoneBuilder.TryGetOrigin())
        public static readonly Vector3 WorldOrigin = new Vector3(-60.005f, 1.20f, -39.705f);

        /// 도면 (x, y, z높이) → 배치도 로컬 Vector3(x, 높이, y). WarehouseLayoutLoader 자식 좌표와 같다.
        public static Vector3 ToUnity(float x, float y, float z = 0f) => new Vector3(x, z, y);

        /// 도면 (x, y, z높이) → Unity 월드 (= WorldOrigin + ToUnity). MQTT 좌표를 씬에 놓을 때 쓴다.
        public static Vector3 ToWorld(float x, float y, float z = 0f) => WorldOrigin + ToUnity(x, y, z);

        /// Unity 배치도 로컬 위치 → 도면 (x, y)
        public static Vector2 ToPlan(Vector3 p) => new Vector2(p.x, p.z);

        /// 도면 (x, y) → Unity 월드, 높이(월드 y)는 그대로 둔다 (AMR 처럼 바닥을 달리는 물체)
        public static Vector3 ToWorldXZ(Vector2 plan, float worldY) =>
            new Vector3(plan.x + WorldOrigin.x, worldY, plan.y + WorldOrigin.z);

        /// Unity 월드 위치 → 도면 (x, y)
        public static Vector2 WorldToPlan(Vector3 p) => new Vector2(p.x - WorldOrigin.x, p.z - WorldOrigin.z);

        /// 도면 theta(rad) → Unity yaw(deg)
        public static float ThetaToYaw(float theta) => Mathf.Repeat(90f - theta * Mathf.Rad2Deg, 360f);

        /// Unity yaw(deg) → 도면 theta(rad, -pi~pi)
        public static float YawToTheta(float yawDeg)
        {
            float t = (90f - yawDeg) * Mathf.Deg2Rad;
            return Mathf.Atan2(Mathf.Sin(t), Mathf.Cos(t));
        }

        public static Quaternion ThetaToRotation(float theta) => Quaternion.Euler(0f, ThetaToYaw(theta), 0f);
    }
}
