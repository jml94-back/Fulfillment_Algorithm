// MQTT 좌표(도면: x 동, y 북, z 위, theta = 동쪽에서 반시계 rad) ↔ Unity(Y-up, 왼손, yaw = +Z 에서 시계 deg).
using UnityEngine;

namespace Warehouse.Mqtt
{
    public static class WhCoords
    {
        /// 도면 (x, y, z높이) → Unity Vector3(x, 높이, y)
        public static Vector3 ToUnity(float x, float y, float z = 0f) => new Vector3(x, z, y);

        /// Unity 위치 → 도면 (x, y)
        public static Vector2 ToPlan(Vector3 p) => new Vector2(p.x, p.z);

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
