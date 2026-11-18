// 컴파일 확인용 최소 UnityEngine 대역 (실제 Unity 에서는 쓰지 않음)
using System;
using System.Collections;
namespace UnityEngine
{
    public class Object { public static void Destroy(Object o) { } }
    public class Component : Object { public GameObject gameObject; public Transform transform; }
    public class Behaviour : Component { }
    public class MonoBehaviour : Behaviour { public Coroutine StartCoroutine(IEnumerator e) => null; }
    public class GameObject : Object { public Transform transform; public static implicit operator bool(GameObject g) => g != null; }
    public class Transform : Component
    {
        public Vector3 position, localPosition, eulerAngles, forward; public Quaternion rotation;
        public void SetParent(Transform p, bool w) { }
        public static implicit operator bool(Transform t) => t != null;
    }
    public class Coroutine { }
    public class YieldInstruction { }
    public class WaitForSeconds : YieldInstruction { public WaitForSeconds(float s) { } }
    public class HeaderAttribute : Attribute { public HeaderAttribute(string s) { } }
    public struct LayerMask { public int value; public static implicit operator LayerMask(int v) => new LayerMask { value = v }; public static implicit operator int(LayerMask m) => m.value; }
    public struct RaycastHit { public float distance; }
    public static class Physics { public static bool Raycast(Vector3 o, Vector3 d, out RaycastHit h, float max, int mask) { h = default; return false; } }
    public static class Debug { public static void Log(object o) { } public static void LogWarning(object o) { } public static void LogException(Exception e) { } }
    public static class Time { public static double timeAsDouble; public static float time, unscaledTime, deltaTime, timeScale; }
    public static class Mathf
    {
        public const float Rad2Deg = 57.29578f, Deg2Rad = 0.01745329f;
        public static float Repeat(float t, float l) => t - (float)Math.Floor(t / l) * l;
        public static float Atan2(float y, float x) => (float)Math.Atan2(y, x);
        public static float Sin(float a) => (float)Math.Sin(a); public static float Cos(float a) => (float)Math.Cos(a);
        public static float Max(float a, float b) => Math.Max(a, b); public static float Abs(float a) => Math.Abs(a);
        public static float Round(float a) => (float)Math.Round(a); public static float Clamp01(float a) => Math.Clamp(a, 0, 1);
        public static float Lerp(float a, float b, float t) => a + (b - a) * Clamp01(t);
    }
    public struct Vector2
    {
        public float x, y; public Vector2(float x, float y) { this.x = x; this.y = y; }
        public float sqrMagnitude => x * x + y * y;
        public static Vector2 operator +(Vector2 a, Vector2 b) => new Vector2(a.x + b.x, a.y + b.y);
        public static Vector2 operator -(Vector2 a, Vector2 b) => new Vector2(a.x - b.x, a.y - b.y);
        public static Vector2 operator *(Vector2 a, float s) => new Vector2(a.x * s, a.y * s);
        public static float Distance(Vector2 a, Vector2 b) => (float)Math.Sqrt((a - b).sqrMagnitude);
        public static Vector2 MoveTowards(Vector2 a, Vector2 b, float d) => b;
    }
    public struct Vector3
    {
        public float x, y, z; public Vector3(float x, float y, float z) { this.x = x; this.y = y; this.z = z; }
        public static Vector3 up => new Vector3(0, 1, 0); public static Vector3 zero => default;
        public static Vector3 operator +(Vector3 a, Vector3 b) => new Vector3(a.x + b.x, a.y + b.y, a.z + b.z);
        public static Vector3 operator *(Vector3 a, float s) => new Vector3(a.x * s, a.y * s, a.z * s);
    }
    public struct Quaternion
    {
        public static Quaternion Euler(float x, float y, float z) => default;
        public static Vector3 operator *(Quaternion q, Vector3 v) => v;
    }
}
