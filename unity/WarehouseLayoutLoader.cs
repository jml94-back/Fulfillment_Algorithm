// D4 배치 JSON(d4_layout.json)을 읽어 씬에 회색 박스(Cube)로 깔아 주는 스크립트.
// 사용법: 빈 GameObject에 붙이고 layoutJson 칸에 d4_layout.json(TextAsset)을 끌어다 놓은 뒤
//         인스펙터의 ⋮ 메뉴 → "Build Layout" 또는 플레이 시작.
// 좌표: 1 unit = 1 m, +X 동, +Y 위, +Z 북. JSON 좌표는 배치도 좌표(서쪽 벽 외부면 · 바닥 윗면 · 남쪽 벽 안쪽면 기준)이고,
//       snapToWorldOrigin 이 켜져 있으면 이 오브젝트를 Unity 월드 (-60.005, 1.20, -39.705)
//       (= DockZoneBuilder.TryGetOrigin()) 에 놓아 기존 씬(DockZoneBuilder, ShuttleRackSystem)과 겹치게 한다. 박스는 회전 없음.
// 실제 에셋(랙, 리프트 모델 등)으로 바꿀 때는 box.id / box.type 으로 찾아 위치·크기만 가져다 쓰면 된다.
using System;
using System.Collections.Generic;
using UnityEngine;

public class WarehouseLayoutLoader : MonoBehaviour
{
    public TextAsset layoutJson;
    [Tooltip("높이 0.02 m 이하 바닥 표시(구역, 적치 칸 등)도 만들지")]
    public bool includeFloorMarkings = true;
    [Tooltip("건물 밖 트럭 정차 칸도 만들지")]
    public bool includeOutdoor = true;
    [Tooltip("벽·건물 높이 박스(벽, 사무실)를 만들지. 위에서 보기 불편하면 끄기")]
    public bool includeWalls = true;
    public bool drawNavGizmos = true;
    [Tooltip("이 오브젝트를 배치도 원점 = Unity 월드 (-60.005, 1.20, -39.705) 로 옮김 (DockZoneBuilder 와 같은 기준)")]
    public bool snapToWorldOrigin = true;
    public static readonly Vector3 WorldOrigin = new Vector3(-60.005f, 1.20f, -39.705f);

    /// 배치도 좌표 (fromWest, 높이, depth) → Unity 월드
    public static Vector3 PlanToWorld(float x, float h, float z) => WorldOrigin + new Vector3(x, h, z);

    [Serializable] public class Box { public string id, type, label, color; public float[] center, size; public bool opening, outdoor; }
    [Serializable] public class NavNode { public string id, kind; public float[] pos; }
    [Serializable] public class NavEdge { public string from, to; public float length; }
    [Serializable] public class Nav { public NavNode[] nodes; public NavEdge[] edges; }
    [Serializable] public class Layout { public Box[] boxes; public Nav nav; }

    Layout _layout;
    readonly Dictionary<string, Material> _mats = new Dictionary<string, Material>();

    void Start() { if (transform.childCount == 0) BuildLayout(); }

    [ContextMenu("Build Layout")]
    public void BuildLayout()
    {
        if (layoutJson == null) { Debug.LogError("layoutJson 이 비어 있음"); return; }
        _layout = JsonUtility.FromJson<Layout>(layoutJson.text);
        for (int i = transform.childCount - 1; i >= 0; i--) DestroyImmediate(transform.GetChild(i).gameObject);
        if (snapToWorldOrigin) { transform.position = WorldOrigin; transform.rotation = Quaternion.identity; transform.localScale = Vector3.one; }

        var groups = new Dictionary<string, Transform>();
        int n = 0;
        foreach (var b in _layout.boxes)
        {
            if (b.opening) continue;                                   // 도크 문 = 벽의 빈 구멍
            if (!includeOutdoor && b.outdoor) continue;
            if (!includeFloorMarkings && b.size[1] <= 0.02f) continue;
            if (!includeWalls && (b.type == "wall" || b.type == "office")) continue;

            if (!groups.TryGetValue(b.type, out var parent))
            {
                parent = new GameObject(b.type).transform;
                parent.SetParent(transform, false);
                groups[b.type] = parent;
            }
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = b.id;
            go.transform.SetParent(parent, false);
            go.transform.localPosition = new Vector3(b.center[0], b.center[1], b.center[2]);
            go.transform.localScale = new Vector3(b.size[0], Mathf.Max(b.size[1], 0.005f), b.size[2]);
            go.GetComponent<Renderer>().sharedMaterial = Mat(b.color);
            n++;
        }
        Debug.Log($"D4 layout: {n} boxes");
    }

    Material Mat(string hex)
    {
        if (_mats.TryGetValue(hex, out var m)) return m;
        var shader = Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
        m = new Material(shader);
        if (ColorUtility.TryParseHtmlString(hex, out var c)) { m.color = c; if (m.HasProperty("_BaseColor")) m.SetColor("_BaseColor", c); }
        _mats[hex] = m;
        return m;
    }

    void OnDrawGizmos()
    {
        if (!drawNavGizmos || layoutJson == null) return;
        if (_layout == null) _layout = JsonUtility.FromJson<Layout>(layoutJson.text);
        if (_layout.nav == null) return;
        var pos = new Dictionary<string, Vector3>();
        foreach (var nd in _layout.nav.nodes)
        {
            var p = transform.TransformPoint(new Vector3(nd.pos[0], 0.05f, nd.pos[2]));
            pos[nd.id] = p;
            Gizmos.color = nd.kind == "aisle" ? Color.red : Color.yellow;
            Gizmos.DrawSphere(p, nd.kind == "aisle" ? 0.25f : 0.4f);
        }
        Gizmos.color = Color.red;
        foreach (var e in _layout.nav.edges) Gizmos.DrawLine(pos[e.from], pos[e.to]);
    }
}
