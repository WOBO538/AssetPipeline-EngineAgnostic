// EAP · Unity 消费端（阶段一模板）
// 读同一份 manifest.json → 建 Material → 映射贴图（含 ORM→MetallicGloss 重排）→ 建 Prefab → 挂 Collider。
//
// 依赖：Newtonsoft Json（manifest 的 textures 是字典，JsonUtility 不支持字典）
//   在 Packages/manifest.json 加： "com.unity.nuget.newtonsoft-json": "3.2.1"
// 用法：菜单 Tools → EAP → Import Manifest…  或
//   Unity -batchmode -quit -executeMethod EAP.ManifestImporter.ImportFromArgs -eapManifest <path>
using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using UnityEditor;
using UnityEngine;

namespace EAP
{
    [Serializable] public class Manifest { public string manifestVersion; public string pipelineId; public string units; public List<Asset> assets; }
    [Serializable] public class Asset
    {
        public string id, name, category, type;
        public Model model;
        public Dictionary<string, Tex> textures;
        public MaterialDef material;
        public Collision collision;
        public Dictionary<string, EngineTarget> engineTargets;
        public List<Check> checks;
    }
    [Serializable] public class Model { public string path, format, unit, upAxis, forwardAxis; public int triCount; public BBox boundingBoxM; }
    [Serializable] public class BBox { public float[] min, max; }
    [Serializable] public class Tex { public string path; public bool srgb; public int[] size; public string channels; }
    [Serializable] public class MaterialDef { public string template, unityShader; public Dictionary<string, float> @params; }
    [Serializable] public class Collision { public string mode; }
    [Serializable] public class EngineTarget { public string destPath, shader, materialInstance; public Dictionary<string, string> propertyMap; }
    [Serializable] public class Check { public string rule; public bool pass; }

    public static class ManifestImporter
    {
        const string DefaultShader = "Universal Render Pipeline/Lit";
        // 语义通道 → URP 默认属性（manifest 的 propertyMap 可覆盖）
        static readonly Dictionary<string, string> DefaultPropMap = new Dictionary<string, string>
        {
            { "BaseColor", "_BaseMap" }, { "Normal", "_BumpMap" }, { "ORM", "_MetallicGlossMap" },
            { "AO", "_OcclusionMap" }, { "Height", "_ParallaxMap" },
        };

        [MenuItem("Tools/EAP/Import Manifest…")]
        public static void ImportDialog()
        {
            string path = EditorUtility.OpenFilePanel("选择 manifest.json", "", "json");
            if (!string.IsNullOrEmpty(path)) Import(path);
        }

        public static void ImportFromArgs()
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == "-eapManifest") { Import(args[i + 1]); return; }
            Debug.LogError("[EAP] 缺少 -eapManifest 参数");
            EditorApplication.Exit(1);
        }

        public static void Import(string manifestPath)
        {
            string repoRoot = Directory.GetParent(Path.GetDirectoryName(Path.GetFullPath(manifestPath))).FullName;
            var manifest = JsonConvert.DeserializeObject<Manifest>(File.ReadAllText(manifestPath));
            if (manifest == null || manifest.assets == null || manifest.assets.Count == 0)
            { Debug.LogError("[EAP] manifest 为空或结构不符"); return; }
            if (!string.Equals(manifest.manifestVersion?.Split('.')[0], "1"))
            { Debug.LogError("[EAP] 不支持的 manifestVersion: " + manifest.manifestVersion); return; }

            int ok = 0, failed = 0;
            var report = new List<string> { "# EAP Unity report  " + DateTime.Now.ToString("s") };
            foreach (var a in manifest.assets)
            {
                try
                {
                    EngineTarget tgt;
                    if (a.engineTargets == null || !a.engineTargets.TryGetValue("Unity", out tgt))
                    { report.Add("- SKIP " + a.id + " (skipped_no_target)"); continue; }
                    EnsureFolder(tgt.destPath);
                    Material mat = BuildMaterial(a, tgt, repoRoot);
                    GameObject prefabRoot = BuildPrefab(a, tgt, mat, repoRoot, report);
                    report.Add($"- OK {a.id} mat={AssetDatabase.GetAssetPath(mat)} prefab={AssetDatabase.GetAssetPath(prefabRoot)}");
                    ok++;
                }
                catch (Exception e)
                { report.Add("- FAIL " + a.id + " : " + e.Message); failed++; Debug.LogError("[EAP] " + a.id + " " + e); }
            }
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            string outPath = Path.ChangeExtension(manifestPath, ".unity_report.md");
            File.WriteAllText(outPath, string.Join("\n", report));
            Debug.Log($"[EAP] 完成 ok={ok} failed={failed} → {outPath}");
        }

        static void EnsureFolder(string assetPath)
        {
            string[] parts = assetPath.TrimEnd('/').Split('/');
            string cur = parts[0]; // "Assets"
            for (int i = 1; i < parts.Length; i++)
            {
                string next = cur + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(cur, parts[i]);
                cur = next;
            }
        }

        static Material BuildMaterial(Asset a, EngineTarget tgt, string repoRoot)
        {
            string shaderName = tgt.shader ?? a.material?.unityShader ?? DefaultShader;
            Shader shader = Shader.Find(shaderName);
            if (shader == null) { Debug.LogWarning("[EAP] 找不到 shader，回退 Lit: " + shaderName); shader = Shader.Find(DefaultShader); }
            var mat = new Material(shader) { name = "M_" + a.id };
            var map = tgt.propertyMap ?? DefaultPropMap;

            foreach (var kv in a.textures ?? new Dictionary<string, Tex>())
            {
                string texAssetPath = Path.Combine(repoRoot, kv.Value.path).Replace('\\', '/');
                ConfigureImporter(texAssetPath, kv.Key, kv.Value.srgb);
                var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(texAssetPath);
                if (tex == null) { Debug.LogWarning("[EAP] 贴图未导入: " + texAssetPath); continue; }
                if (kv.Key == "ORM") { ApplyOrm(mat, map, tex, a.id); continue; }
                string prop = map.ContainsKey(kv.Key) ? map[kv.Key] : DefaultPropMap[kv.Key];
                if (mat.HasProperty(prop)) mat.SetTexture(prop, tex);
                else Debug.LogWarning($"[EAP] {a.id} 材质无属性 {prop}（channel={kv.Key}）");
            }
            if (a.material?.@params != null)
                foreach (var kv in a.material.@params)
                    if (mat.HasProperty(kv.Key)) mat.SetFloat(kv.Key, kv.Value);

            string matPath = (tgt.destPath ?? "Assets/EAP").TrimEnd('/') + "/" + mat.name + ".mat";
            AssetDatabase.CreateAsset(mat, matPath);
            return mat;
        }

        // ORM(R=AO,G=Roughness,B=Metallic) → URP: R=Metallic, A=Smoothness(1-G)；AO 另存给 _OcclusionMap
        static void ApplyOrm(Material mat, Dictionary<string, string> map, Texture2D src, string id)
        {
            Texture2D readable = MakeReadable(src);
            if (readable == null) { Debug.LogWarning("[EAP] " + id + " ORM 不可读，跳过重排（需勾 Read/Write）"); return; }
            int w = readable.width, h = readable.height;
            var mg = new Texture2D(w, h, TextureFormat.RGBA32, false) { name = "T_" + id + "_MG" };
            var ao = new Texture2D(w, h, TextureFormat.RGBA32, false) { name = "T_" + id + "_AO" };
            var sp = readable.GetPixels();
            var mgPx = new Color[sp.Length]; var aoPx = new Color[sp.Length];
            for (int i = 0; i < sp.Length; i++)
            {
                float metallic = sp[i].b, roughness = sp[i].g, occlusion = sp[i].r;
                mgPx[i] = new Color(metallic, metallic, metallic, 1f - roughness);
                aoPx[i] = new Color(occlusion, occlusion, occlusion, 1f);
            }
            mg.SetPixels(mgPx); mg.Apply();
            ao.SetPixels(aoPx); ao.Apply();
            SaveTexture(mg, src); SaveTexture(ao, src);
            string mgProp = map.ContainsKey("ORM") ? map["ORM"] : "_MetallicGlossMap";
            if (mat.HasProperty(mgProp)) mat.SetTexture(mgProp, mg);
            if (mat.HasProperty("_OcclusionMap")) mat.SetTexture("_OcclusionMap", ao);
        }

        static Texture2D MakeReadable(Texture2D src)
        {
            string path = AssetDatabase.GetAssetPath(src);
            var imp = AssetImporter.GetAtPath(path) as TextureImporter;
            if (imp != null && !imp.isReadable) { imp.isReadable = true; imp.SaveAndReimport(); }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }

        static void SaveTexture(Texture2D tex, Texture2D near)
        {
            string dir = Path.GetDirectoryName(AssetDatabase.GetAssetPath(near))?.Replace('\\', '/');
            string path = dir + "/" + tex.name + ".asset";
            AssetDatabase.CreateAsset(tex, path);
        }

        static void ConfigureImporter(string assetPath, string channel, bool srgb)
        {
            var imp = AssetImporter.GetAtPath(assetPath) as TextureImporter;
            if (imp == null) return;
            bool changed = false;
            if (channel == "Normal" && imp.textureType != TextureImporterType.NormalMap)
            { imp.textureType = TextureImporterType.NormalMap; changed = true; }
            if (channel != "Normal" && imp.sRGBTexture != srgb)
            { imp.sRGBTexture = srgb; changed = true; }
            if (changed) imp.SaveAndReimport();
        }

        static GameObject BuildPrefab(Asset a, EngineTarget tgt, Material mat, string repoRoot, List<string> report)
        {
            string modelPath = Path.Combine(repoRoot, a.model.path).Replace('\\', '/');
            var mesh = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            if (mesh == null) { Debug.LogWarning("[EAP] 模型未找到，只输出材质: " + modelPath); return null; }
            var go = (GameObject)PrefabUtility.InstantiatePrefab(mesh);
            var renderer = go.GetComponent<Renderer>();
            if (renderer == null) renderer = go.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = mat;
            AddCollider(go, a);
            string prefabPath = (tgt.destPath ?? "Assets/EAP").TrimEnd('/') + "/" + a.id + ".prefab";
            var prefab = PrefabUtility.SaveAsPrefabAsset(go, prefabPath);
            UnityEngine.Object.DestroyImmediate(go);
            return prefab;
        }

        static void AddCollider(GameObject go, Asset a)
        {
            string mode = a.collision?.mode ?? "box";
            if (mode == "none") return;
            var bbox = a.model?.boundingBoxM;
            if (mode == "auto_convex")
            {
                var mf = go.GetComponentInChildren<MeshFilter>();
                if (mf != null) { var mc = go.AddComponent<MeshCollider>(); mc.sharedMesh = mf.sharedMesh; mc.convex = true; return; }
            }
            var box = go.AddComponent<BoxCollider>();
            if (bbox != null && bbox.min != null && bbox.max != null)
            {
                box.size = new Vector3(bbox.max[0] - bbox.min[0], bbox.max[2] - bbox.min[2], bbox.max[1] - bbox.min[1]);
                box.center = new Vector3((bbox.max[0] + bbox.min[0]) / 2f, (bbox.max[2] + bbox.min[2]) / 2f, (bbox.max[1] + bbox.min[1]) / 2f);
            }
        }
    }
}
