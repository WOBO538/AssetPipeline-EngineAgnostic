// EAP · Unity 消费端（空工程实跑用·管线自适应版）
// 与仓库 unity/Editor/ManifestImporter.cs 逻辑一致，唯一区别：
//   - 自动识别 URP / Built-in：找到 "Universal Render Pipeline/Lit" 就用 URP 属性，否则回退 "Standard"；
//   - 材质参数按管线映射（RoughnessScale→_Smoothness/_Glossiness，MetallicScale→_Metallic，NormalStrength→_BumpScale）。
// 依赖：Newtonsoft Json（com.unity.nuget.newtonsoft-json）
// 用法：菜单 Tools → EAP → Import Manifest…（或 -executeMethod EAP.ManifestImporter.ImportFromArgs -eapManifest <path>）
// 落位：-executeMethod EAP.ManifestImporter.PlaceFromArgs / VerifyPlacementsFromArgs -eapManifest <path>
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using Newtonsoft.Json;
using UnityEditor;
using UnityEditor.SceneManagement;
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
        public Placements placements;
        public Dictionary<string, EngineTarget> engineTargets;
        public List<Check> checks;
        public LodDef lod;   // v1.12 可选：4 级 LOD 模型清单
    }
    [Serializable] public class Model { public string path, format, unit, upAxis, forwardAxis; public int triCount; public BBox boundingBoxM; }
    [Serializable] public class BBox { public float[] min, max; }
    [Serializable] public class Tex { public string path, channels, source; public bool srgb; public int[] size; }
    [Serializable] public class MaterialDef { public string template, unityShader; public Dictionary<string, float> @params; }
    [Serializable] public class Collision { public string mode; public CollisionUnity unity; }
    [Serializable] public class CollisionUnity { public string collider; }
    [Serializable] public class Placements { public string space, unit; public int count; public List<PlacementItem> items; }
    [Serializable] public class PlacementItem { public float[] t, r, s; }
    [Serializable] public class EngineTarget { public string destPath, shader, materialInstance, modelPath, materialPathPrefix; public Dictionary<string, string> propertyMap; }
    [Serializable] public class Check { public string rule; public bool pass; }
    // v1.12：可选 lod 字段（老 manifest 没有 → 不建 LODGroup，行为不变）
    [Serializable] public class LodDef { public int levels; public string source; public float[] screenHeights; public List<LodModel> models; public string note; }
    [Serializable] public class LodModel { public int index; public string path; public int triCount; }
    [Serializable] public class LodReadback
    {
        public int levels;
        public List<int> triCounts = new List<int>();
        public List<float> screenHeights = new List<float>();
    }
    [Serializable] public class VerifyDoc
    {
        public string schema = "eap.unity_verify/1";
        public string pipeline, manifest;
        // v1.11：内部时间戳（本地时区偏移），供 --parity-check 判定新鲜度；clone/checkout 不改变它
        public string generatedAt;
        public List<VerifyAsset> assets = new List<VerifyAsset>();
    }
    [Serializable] public class VerifyAsset
    {
        public string id, prefab, shader;
        public bool prefabExists;
        public int meshTriCount;
        public BBox boundsM;
        public Dictionary<string, string> textures = new Dictionary<string, string>();
        public Dictionary<string, bool> texturesApplied = new Dictionary<string, bool>();
        // v1.12：LODGroup 读回（无 LODGroup 时为 null）
        public LodReadback lod;
    }
    [Serializable] public class PlacementsVerifyDoc
    {
        public string schema = "eap.unity_placements_verify/1";
        public string manifest;
        // v1.11：内部时间戳（本地时区偏移），供 --parity-check 判定新鲜度；clone/checkout 不改变它
        public string generatedAt;
        public string scene = ManifestImporter.PlaceScenePath;
        public bool sceneExists;
        public Dictionary<string, int> instances = new Dictionary<string, int>();
        public int total;
        public BBox bboxM;
        public int colliders;
        public int unknownInstances;
        public List<SampleMaterial> sampleMaterials = new List<SampleMaterial>();
        public List<InstanceDetail> instancesDetail = new List<InstanceDetail>();
    }
    [Serializable] public class SampleMaterial { public string instance, baseColor; }
    [Serializable] public class InstanceDetail
    {
        public string assetId;
        public int index;
        public float[] t, s;
        public float yawDeg;
        public bool matched;
    }
    [Serializable] public class PerfSample
    {
        public string segment = "C_collision_unity";
        public int round;
        public string coldWarm = "n/a";   // Unity 侧无 DDC 概念，如实标 n/a
        public double durationS;
        public List<double> perPrefab = new List<double>();
        public int prefabs, colliders, convexColliders, bakeCalls, bakeErrors;
        public string note;
    }
    [Serializable] public class PerfDoc
    {
        public string schema = "eap.perf.unity/1";
        public string manifest;
        public int rounds;
        public List<PerfSample> samples = new List<PerfSample>();
    }

    public static class ManifestImporter
    {
        const string UrpShader = "Universal Render Pipeline/Lit";
        const string BuiltInShader = "Standard";
        public const string PlaceScenePath = "Assets/Scenes/EAP_PlacedTrees.unity";
        const string PlacementRootName = "EAP_Placements";

        static readonly Dictionary<string, string> UrpPropMap = new Dictionary<string, string>
        {
            { "BaseColor", "_BaseMap" }, { "Normal", "_BumpMap" }, { "ORM", "_MetallicGlossMap" },
            { "AO", "_OcclusionMap" }, { "Height", "_ParallaxMap" },
        };
        static readonly Dictionary<string, string> BuiltInPropMap = new Dictionary<string, string>
        {
            { "BaseColor", "_MainTex" }, { "Normal", "_BumpMap" }, { "ORM", "_MetallicGlossMap" },
            { "AO", "_OcclusionMap" }, { "Height", "_ParallaxMap" },
        };

        static bool _isUrp;
        static Shader _shader;
        static Dictionary<string, string> _pipePropMap;

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
                if (args[i] == "-eapManifest")
                {
                    try { Import(args[i + 1]); EditorApplication.Exit(0); }
                    catch (Exception e) { Debug.LogError("[EAP] import 失败: " + e); EditorApplication.Exit(1); }
                    return;
                }
            Debug.LogError("[EAP] 缺少 -eapManifest 参数");
            EditorApplication.Exit(1);
        }

        public static void VerifyFromArgs()
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == "-eapManifest")
                {
                    try { Verify(args[i + 1]); EditorApplication.Exit(0); }
                    catch (Exception e) { Debug.LogError("[EAP] verify 失败: " + e); EditorApplication.Exit(1); }
                    return;
                }
            Debug.LogError("[EAP] 缺少 -eapManifest 参数");
            EditorApplication.Exit(1);
        }

        public static void PlaceFromArgs()
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == "-eapManifest")
                {
                    try { Place(args[i + 1]); EditorApplication.Exit(0); }
                    catch (Exception e) { Debug.LogError("[EAP] place 失败: " + e); EditorApplication.Exit(1); }
                    return;
                }
            Debug.LogError("[EAP] 缺少 -eapManifest 参数");
            EditorApplication.Exit(1);
        }

        public static void VerifyPlacementsFromArgs()
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == "-eapManifest")
                {
                    try { VerifyPlacements(args[i + 1]); EditorApplication.Exit(0); }
                    catch (Exception e) { Debug.LogError("[EAP] placements verify 失败: " + e); EditorApplication.Exit(1); }
                    return;
                }
            Debug.LogError("[EAP] 缺少 -eapManifest 参数");
            EditorApplication.Exit(1);
        }

        static string ArgValue(string[] args, string name)
        {
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == name) return args[i + 1];
            return null;
        }

        /// <summary>性能段 C（Unity 侧）：20 个 prefab 副本各加 MeshCollider(convex) + Physics.BakeMesh。
        ///
        /// 计时边界：每个 prefab 从"实例化副本"开始，到"加完 convex MeshCollider 并强制烘焙"结束
        /// （含实例化与销毁副本实例；不含 prefab 副本复制、不含模型导入/材质）。
        /// 用一个 20 份的循环重复 --eapPerfRounds 次，每轮写一条采样。
        /// 网格 Read/Write 若关闭，Physics.BakeMesh 无法烘焙 → 计时前先把 manifest 引用的模型
        /// importer 打开 isReadable（只动 Unity 工程内导入设置，仓库资产不动），并在日志里说明。
        /// </summary>
        public static void PerfCollisionFromArgs()
        {
            string[] args = Environment.GetCommandLineArgs();
            string manifestPath = ArgValue(args, "-eapManifest");
            string outPath = ArgValue(args, "-eapPerfOut");
            int rounds = 3;
            int.TryParse(ArgValue(args, "-eapPerfRounds") ?? "3", out rounds);
            try
            {
                if (string.IsNullOrEmpty(manifestPath) || string.IsNullOrEmpty(outPath))
                    throw new Exception("缺 -eapManifest 或 -eapPerfOut");
                RunPerfCollision(manifestPath, outPath, rounds);
                EditorApplication.Exit(0);
            }
            catch (Exception e)
            {
                Debug.LogError("[EAP] perf collision 失败: " + e);
                EditorApplication.Exit(1);
            }
        }

        static void RunPerfCollision(string manifestPath, string outPath, int rounds)
        {
            DetectPipeline();
            var manifest = JsonConvert.DeserializeObject<Manifest>(File.ReadAllText(manifestPath));
            if (manifest == null || manifest.assets == null)
                throw new Exception("manifest 为空或结构不符: " + manifestPath);
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            var prefabs = new List<string>();
            var models = new HashSet<string>();
            foreach (var a in manifest.assets)
            {
                EngineTarget tgt;
                if (a.engineTargets == null || !a.engineTargets.TryGetValue("Unity", out tgt)) continue;
                string pp = PrefabAssetPath(tgt, a);
                if (!File.Exists(Path.Combine(projectRoot, pp)))
                    throw new Exception("源 prefab 不存在（先跑 --unity-check）: " + pp);
                prefabs.Add(pp);
                string declared = !string.IsNullOrEmpty(tgt.modelPath) ? tgt.modelPath : a.model?.path;
                if (!string.IsNullOrEmpty(declared)) models.Add(ModelAssetPath(declared));
            }
            if (prefabs.Count == 0) throw new Exception("manifest 里没有 Unity 目标资产");
            foreach (var m in models)
            {
                var imp = AssetImporter.GetAtPath(m) as ModelImporter;
                if (imp != null && !imp.isReadable)
                {
                    imp.isReadable = true;
                    imp.SaveAndReimport();
                    Debug.Log("[EAP] perf C: 打开 Read/Write 以便烘焙凸包 → " + m);
                }
            }

            const int PrefabsPerSample = 20;
            EnsureFolder("Assets/EAP/Perf");
            var doc = new PerfDoc { manifest = Path.GetFileName(manifestPath), rounds = rounds };
            for (int r = 1; r <= rounds; r++)
            {
                var copies = new List<string>();
                for (int i = 0; i < PrefabsPerSample; i++)
                {
                    string copy = "Assets/EAP/Perf/C_r" + r + "_" + i.ToString("00") + ".prefab";
                    AssetDatabase.DeleteAsset(copy);
                    if (!AssetDatabase.CopyAsset(prefabs[i % prefabs.Count], copy))
                        throw new Exception("prefab 副本失败: " + copy);
                    AssetDatabase.ImportAsset(copy, ImportAssetOptions.ForceSynchronousImport);
                    copies.Add(copy);
                }
                var s = new PerfSample
                {
                    round = r,
                    note = "20 个 prefab 副本（conifer/broadleaf 各半）各加 MeshCollider(convex)+Physics.BakeMesh；"
                         + "副本自带 collider 先摘掉避免重复；不写回 prefab（运行时网格不能序列化）",
                };
                double total = 0;
                foreach (var copy in copies)
                {
                    var sw = System.Diagnostics.Stopwatch.StartNew();
                    var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(copy);
                    if (prefab == null) throw new Exception("加载副本失败: " + copy);
                    var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
                    try
                    {
                        foreach (var c in go.GetComponentsInChildren<Collider>(true))
                            UnityEngine.Object.DestroyImmediate(c);
                        int added = 0;
                        foreach (var mf in go.GetComponentsInChildren<MeshFilter>(true))
                        {
                            if (mf.sharedMesh == null) continue;
                            var m = UnityEngine.Object.Instantiate(mf.sharedMesh);
                            m.name = "EAP_PerfCook_r" + r + "_" + s.prefabs;
                            var mc = mf.gameObject.AddComponent<MeshCollider>();
                            mc.sharedMesh = m;
                            mc.convex = true;
                            // Unity 2022.3 的 Physics.BakeMesh 返回 void（无法回读成功与否），
                            // 以"调用未抛异常 + 碰撞体 convex/网格读回"当证据，见文档说明。
                            try
                            {
                                Physics.BakeMesh(m.GetInstanceID(), true);
                                s.bakeCalls++;
                            }
                            catch (Exception be)
                            {
                                s.bakeErrors++;
                                Debug.LogWarning("[EAP] Physics.BakeMesh 失败: " + be.Message);
                            }
                            s.colliders++;
                            if (mc.convex && mc.sharedMesh != null) s.convexColliders++;
                            added++;
                            UnityEngine.Object.DestroyImmediate(m);
                        }
                        if (added == 0) throw new Exception("副本没有可加碰撞的网格: " + copy);
                    }
                    finally { UnityEngine.Object.DestroyImmediate(go); }
                    sw.Stop();
                    double sec = sw.Elapsed.TotalSeconds;
                    s.perPrefab.Add(Math.Round(sec, 6));
                    total += sec;
                    s.prefabs++;
                }
                s.durationS = Math.Round(total, 6);
                doc.samples.Add(s);
                Debug.Log(string.Format("[EAP] perf C round={0} prefabs={1} colliders={2} convex={3} bakeCalls={4} bakeErrors={5} {6:F3}s",
                    r, s.prefabs, s.colliders, s.convexColliders, s.bakeCalls, s.bakeErrors, s.durationS));
                foreach (var copy in copies) AssetDatabase.DeleteAsset(copy);
                AssetDatabase.Refresh();
            }
            File.WriteAllText(outPath, JsonConvert.SerializeObject(doc, Formatting.Indented));
            Debug.Log("[EAP] perf C → " + outPath + " rounds=" + doc.samples.Count);
        }

        /// <summary>UE(Z-up) → Unity(Y-up) 唯一权威换算：unityPos = (x, z, y)。</summary>
        static Vector3 UeToUnityPos(float[] t)
            => new Vector3(t[0], t[2], t[1]);

        /// <summary>UE yaw（度）→ Unity yaw：Y 轴取反，避免镜像。</summary>
        static float UeToUnityYaw(float[] r)
            => (r != null && r.Length >= 3) ? -r[2] : 0f;

        /// <summary>Prefab 在工程内的落点（与 BuildPrefab 的命名规则一致）。</summary>
        static string PrefabAssetPath(EngineTarget tgt, Asset a)
            => (tgt.destPath ?? "Assets/EAP").TrimEnd('/') + "/" + a.id + ".prefab";

        /// <summary>贴图在 Unity 工程内的落点（manifest 里是仓库相对路径，Unity 只认 Assets/）。</summary>
        static string TextureAssetPath(string rel)
        {
            if (string.IsNullOrEmpty(rel)) return rel;
            string p = rel.Replace('\\', '/');
            if (p.StartsWith("Assets/", StringComparison.Ordinal)) return p;
            return "Assets/EAP/Trees/Textures/" + Path.GetFileName(p);
        }

        /// <summary>模型在 Unity 工程内的落点（fbx 由 Python/Blender 产出后复制进来）。</summary>
        static string ModelAssetPath(string rel)
        {
            if (string.IsNullOrEmpty(rel)) return rel;
            string p = rel.Replace('\\', '/');
            if (p.StartsWith("Assets/", StringComparison.Ordinal)) return p;
            return "Assets/EAP/Trees/Models/" + Path.GetFileName(p);
        }

        static string Sha256File(string path)
        {
            using (var sha = SHA256.Create())
            using (var fs = File.OpenRead(path))
                return BitConverter.ToString(sha.ComputeHash(fs)).Replace("-", "").ToLowerInvariant();
        }

        /// <summary>把仓库里的贴图/模型复制进工程；内容哈希相同就不覆盖（不弄脏工程）。</summary>
        static void SyncExternalAssets(Manifest manifest, string repoRoot, List<string> report)
        {
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            int copied = 0, kept = 0;
            foreach (var a in manifest.assets)
            {
                EngineTarget tgt;
                if (a.engineTargets == null || !a.engineTargets.TryGetValue("Unity", out tgt)) continue;
                var items = new List<KeyValuePair<string, string>>();
                string declared = !string.IsNullOrEmpty(tgt.modelPath) ? tgt.modelPath : a.model?.path;
                if (!string.IsNullOrEmpty(declared)) items.Add(new KeyValuePair<string, string>(declared, ModelAssetPath(declared)));
                // v1.12：LOD1-3 的 fbx 也按内容哈希复制进工程
                if (a.lod != null && a.lod.models != null)
                    foreach (var lm in a.lod.models)
                        if (lm != null && !string.IsNullOrEmpty(lm.path))
                            items.Add(new KeyValuePair<string, string>(lm.path, ModelAssetPath(lm.path)));
                foreach (var kv in a.textures ?? new Dictionary<string, Tex>())
                    if (!string.IsNullOrEmpty(kv.Value.path)) items.Add(new KeyValuePair<string, string>(kv.Value.path, TextureAssetPath(kv.Value.path)));
                foreach (var item in items)
                {
                    string src = Path.GetFullPath(Path.Combine(repoRoot, item.Key));
                    if (!File.Exists(src)) { report.Add("- WARN " + a.id + " 外部文件不存在: " + src); continue; }
                    string dst = Path.Combine(projectRoot, item.Value);
                    Directory.CreateDirectory(Path.GetDirectoryName(dst));
                    if (File.Exists(dst) && Sha256File(src) == Sha256File(dst)) { kept++; continue; }
                    File.Copy(src, dst, true);
                    copied++;
                }
            }
            if (copied + kept > 0) AssetDatabase.Refresh();
            report.Add(string.Format("- sync: copied={0} unchanged={1}", copied, kept));
            Debug.Log(string.Format("[EAP] sync copied={0} unchanged={1}", copied, kept));
        }

        static void DetectPipeline()
        {
            var urp = Shader.Find(UrpShader);
            _isUrp = urp != null;
            _shader = _isUrp ? urp : Shader.Find(BuiltInShader);
            _pipePropMap = _isUrp ? UrpPropMap : BuiltInPropMap;
            Debug.Log("[EAP] pipeline=" + (_isUrp ? "URP" : "BuiltIn") +
                      " shader=" + (_shader != null ? _shader.name : "<null>"));
            if (_shader == null) Debug.LogError("[EAP] 找不到 URP Lit 也找不到 Standard shader");
        }

        public static void Import(string manifestPath)
        {
            DetectPipeline();
            string repoRoot = Directory.GetParent(Path.GetDirectoryName(Path.GetFullPath(manifestPath))).FullName;
            var manifest = JsonConvert.DeserializeObject<Manifest>(File.ReadAllText(manifestPath));
            if (manifest == null || manifest.assets == null || manifest.assets.Count == 0)
            { Debug.LogError("[EAP] manifest 为空或结构不符"); return; }
            if (!string.Equals(manifest.manifestVersion?.Split('.')[0], "1"))
            { Debug.LogError("[EAP] 不支持的 manifestVersion: " + manifest.manifestVersion); return; }

            int ok = 0, failed = 0;
            var report = new List<string> {
                "# EAP Unity report  " + DateTime.Now.ToString("s"),
                "- pipeline: " + (_isUrp ? "URP" : "BuiltIn") + "  shader: " + (_shader != null ? _shader.name : "<null>"),
                "- manifest: " + manifestPath,
                "- manifestVersion: " + manifest.manifestVersion + "  assets: " + manifest.assets.Count,
            };
            SyncExternalAssets(manifest, repoRoot, report);
            foreach (var a in manifest.assets)
            {
                try
                {
                    EngineTarget tgt;
                    if (a.engineTargets == null || !a.engineTargets.TryGetValue("Unity", out tgt))
                    { report.Add("- SKIP " + a.id + " (skipped_no_target)"); continue; }
                    EnsureFolder(tgt.destPath);
                    Material mat = BuildMaterial(a, tgt, repoRoot, report);
                    GameObject prefabRoot = BuildPrefab(a, tgt, mat, repoRoot, report);
                    report.Add($"- OK {a.id} mat={AssetDatabase.GetAssetPath(mat)} prefab={(prefabRoot != null ? AssetDatabase.GetAssetPath(prefabRoot) : "<none: 模型未导入>")}");
                    ok++;
                }
                catch (Exception e)
                { report.Add("- FAIL " + a.id + " : " + e.Message); failed++; Debug.LogError("[EAP] " + a.id + " " + e); }
            }
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            report.Add($"- summary: ok={ok} failed={failed}");
            string outPath = Path.ChangeExtension(manifestPath, ".unity_report.md");
            File.WriteAllText(outPath, string.Join("\n", report));
            Debug.Log($"[EAP] 完成 ok={ok} failed={failed} → {outPath}");
        }

        static void EnsureFolder(string assetPath)
        {
            string[] parts = assetPath.TrimEnd('/').Split('/');
            string cur = parts[0];
            for (int i = 1; i < parts.Length; i++)
            {
                string next = cur + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(cur, parts[i]);
                cur = next;
            }
        }

        static Material BuildMaterial(Asset a, EngineTarget tgt, string repoRoot, List<string> report)
        {
            string shaderName = tgt.shader ?? a.material?.unityShader ?? (_isUrp ? UrpShader : BuiltInShader);
            Shader shader = Shader.Find(shaderName);
            if (shader == null)
            {
                report.Add("- WARN " + a.id + " 找不到 shader " + shaderName + "，回退 " + (_isUrp ? UrpShader : BuiltInShader));
                shader = _shader;
            }
            var mat = new Material(shader) { name = "M_" + a.id };
            var map = tgt.propertyMap ?? _pipePropMap;

            foreach (var kv in a.textures ?? new Dictionary<string, Tex>())
            {
                string texAssetPath = TextureAssetPath(kv.Value.path);
                ConfigureImporter(texAssetPath, kv.Key, kv.Value.srgb);
                var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(texAssetPath);
                if (tex == null) { report.Add("- WARN " + a.id + " 贴图未导入: " + texAssetPath); continue; }
                if (kv.Key == "ORM") { ApplyOrm(mat, map, tex, a.id, report); continue; }
                string prop = map.ContainsKey(kv.Key) ? map[kv.Key] : null;
                if (prop != null && mat.HasProperty(prop)) mat.SetTexture(prop, tex);
                else report.Add("- WARN " + a.id + " 材质无属性 " + prop + "（channel=" + kv.Key + "）");
            }

            if (a.material?.@params != null)
            {
                foreach (var kv in a.material.@params)
                {
                    string p = MapParam(kv.Key);
                    if (p == null) continue;
                    if (mat.HasProperty(p)) mat.SetFloat(p, ConvertParam(kv.Key, kv.Value));
                    else report.Add("- WARN " + a.id + " 无参数属性 " + p + "（" + kv.Key + "）");
                }
            }

            string matPath = (tgt.destPath ?? "Assets/EAP").TrimEnd('/') + "/" + mat.name + ".mat";
            AssetDatabase.CreateAsset(mat, matPath);
            return mat;
        }

        static string MapParam(string name)
        {
            switch (name)
            {
                case "RoughnessScale": return _isUrp ? "_Smoothness" : "_Glossiness";
                case "MetallicScale": return "_Metallic";
                case "NormalStrength": return "_BumpScale";
                default: return name;
            }
        }

        static float ConvertParam(string name, float v)
            => name == "RoughnessScale" ? Mathf.Clamp01(1f - v) : v;

        static void ApplyOrm(Material mat, Dictionary<string, string> map, Texture2D src, string id, List<string> report)
        {
            Texture2D readable = MakeReadable(src);
            if (readable == null) { report.Add("- WARN " + id + " ORM 不可读，跳过重排（需勾 Read/Write）"); return; }
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
            AssetDatabase.CreateAsset(tex, dir + "/" + tex.name + ".asset");
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
            string declared = !string.IsNullOrEmpty(tgt.modelPath) ? tgt.modelPath : a.model?.path;
            string modelPath = ModelAssetPath(declared);
            var mesh = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            if (mesh == null)
            {
                report.Add("- WARN " + a.id + " 模型未找到（Unity 原生不认 .glb；需要 engineTargets.Unity.modelPath 指向 fbx）: " + modelPath + " → 只输出材质");
                Debug.LogWarning("[EAP] 模型未找到，只输出材质: " + modelPath);
                return null;
            }
            var go = (GameObject)PrefabUtility.InstantiatePrefab(mesh);
            var renderers = go.GetComponentsInChildren<Renderer>(true);
            if (renderers.Length == 0) renderers = new Renderer[] { go.AddComponent<MeshRenderer>() };
            foreach (var r in renderers) r.sharedMaterial = mat;
            AddCollider(go, a);   // 必须在加 LOD 子网格之前：碰撞只用 LOD0
            AttachLodGroup(go, a, mat, report);
            string prefabPath = (tgt.destPath ?? "Assets/EAP").TrimEnd('/') + "/" + a.id + ".prefab";
            var prefab = PrefabUtility.SaveAsPrefabAsset(go, prefabPath);
            UnityEngine.Object.DestroyImmediate(go);
            return prefab;
        }

        /// <summary>v1.12：给 prefab 加 4 级 LODGroup。
        ///
        /// LOD0 用原模型自身的 Renderer；LOD1-3 把 fbx 实例的网格子物体摘进 Root 下
        /// （两者同一导出器、同一坐标口径，局部变换取源模型的，不做手工缩放）。
        /// screenHeights 是 0–1 归一化高度，来自 manifest 的可选字段；与 UE 的 screen size
        /// 1.0/0.5/0.25/0.1 一一对应（LOD0 的 1.0 在 LODGroup 里是隐式的，不写）。
        /// </summary>
        static void AttachLodGroup(GameObject root, Asset a, Material mat, List<string> report)
        {
            if (a.lod == null || a.lod.models == null || a.lod.models.Count == 0) return;
            var lod0 = root.GetComponentsInChildren<Renderer>(true);
            if (lod0.Length == 0) { report.Add("- WARN " + a.id + " LOD0 无 Renderer，跳过 LODGroup"); return; }
            var heights = a.lod.screenHeights ?? new float[] { 0.5f, 0.25f, 0.1f, 0f };
            var renderersByLevel = new Dictionary<int, List<Renderer>>();
            renderersByLevel[0] = new List<Renderer>(lod0);
            foreach (var lm in a.lod.models)
            {
                if (lm == null || lm.index <= 0) continue;
                string modelPath = ModelAssetPath(lm.path);
                var src = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
                if (src == null) { report.Add("- WARN " + a.id + " LOD" + lm.index + " 模型未导入: " + modelPath); continue; }
                var srcFilters = src.GetComponentsInChildren<MeshFilter>(true);
                if (srcFilters.Length == 0 || srcFilters[0].sharedMesh == null)
                { report.Add("- WARN " + a.id + " LOD" + lm.index + " 没有网格: " + modelPath); continue; }
                // 复制一份普通 GameObject（不是 prefab 实例，才能把子物体摘到 Root 下）
                var inst = UnityEngine.Object.Instantiate(src);
                inst.name = "LOD" + lm.index;
                inst.transform.SetParent(root.transform, false);
                var list = new List<Renderer>();
                foreach (var mf in inst.GetComponentsInChildren<MeshFilter>(true))
                {
                    if (mf.sharedMesh == null) continue;
                    var mr = mf.GetComponent<MeshRenderer>();
                    if (mr == null) mr = mf.gameObject.AddComponent<MeshRenderer>();
                    var mats = new Material[Mathf.Max(1, mf.sharedMesh.subMeshCount)];
                    for (int i = 0; i < mats.Length; i++) mats[i] = mat;
                    mr.sharedMaterials = mats;
                    list.Add(mr);
                }
                if (list.Count == 0) { UnityEngine.Object.DestroyImmediate(inst); continue; }
                renderersByLevel[lm.index] = list;
            }
            if (renderersByLevel.Count < 2) { report.Add("- WARN " + a.id + " LOD 模型一个都没装上，不建 LODGroup"); return; }

            var levels = new List<LOD>();
            for (int i = 0; i < a.lod.levels; i++)
            {
                List<Renderer> rs;
                if (!renderersByLevel.TryGetValue(i, out rs)) continue;
                float h = (i < heights.Length) ? heights[i] : 0f;
                levels.Add(new LOD(i == a.lod.levels - 1 ? 0f : h, rs.ToArray()));
            }
            var group = root.AddComponent<LODGroup>();
            group.SetLODs(levels.ToArray());
            group.RecalculateBounds();
            report.Add("- LOD " + a.id + " levels=" + levels.Count + " tri=" +
                       string.Join("/", levels.ConvertAll(l => TrisOfRenderers(l.renderers).ToString()).ToArray()));
        }

        static int TrisOfRenderers(Renderer[] rs)
        {
            int t = 0;
            foreach (var r in rs)
            {
                if (r == null) continue;
                var mf = r.GetComponent<MeshFilter>();
                if (mf != null && mf.sharedMesh != null) t += mf.sharedMesh.triangles.Length / 3;
            }
            return t;
        }

        /// <summary>新进程读回：prefab 存在性 / 三角面 / 包围盒 / shader / 贴图是否真的挂上。</summary>
        public static void Verify(string manifestPath)
        {
            DetectPipeline();
            var manifest = JsonConvert.DeserializeObject<Manifest>(File.ReadAllText(manifestPath));
            if (manifest == null || manifest.assets == null)
                throw new Exception("manifest 为空或结构不符: " + manifestPath);
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            var doc = new VerifyDoc { pipeline = _isUrp ? "URP" : "BuiltIn", manifest = Path.GetFileName(manifestPath) };
            foreach (var a in manifest.assets)
            {
                EngineTarget tgt;
                if (a.engineTargets == null || !a.engineTargets.TryGetValue("Unity", out tgt)) continue;
                string prefabPath = (tgt.destPath ?? "Assets/EAP").TrimEnd('/') + "/" + a.id + ".prefab";
                var va = new VerifyAsset { id = a.id, prefab = prefabPath };
                va.prefabExists = File.Exists(Path.Combine(projectRoot, prefabPath));
                foreach (var kv in a.textures ?? new Dictionary<string, Tex>())
                    va.textures[kv.Key] = TextureAssetPath(kv.Value.path);
                if (va.prefabExists)
                {
                    var asset = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                    if (asset != null)
                    {
                        var go = (GameObject)PrefabUtility.InstantiatePrefab(asset);
                        // v1.12：有 LODGroup 时，meshTriCount / bounds 只按 LOD0 的 Renderer 统计
                        // （口径与老门槛一致：源网格面数/包围盒；LOD1-3 另存到 va.lod）
                        var lodGroup = go.GetComponentInChildren<LODGroup>(true);
                        Renderer[] rs;
                        if (lodGroup != null)
                        {
                            var lods = lodGroup.GetLODs();
                            va.lod = new LodReadback { levels = lods.Length };
                            foreach (var l in lods)
                            {
                                va.lod.triCounts.Add(TrisOfRenderers(l.renderers));
                                va.lod.screenHeights.Add((float)Math.Round(l.screenRelativeTransitionHeight, 6));
                            }
                            rs = (lods.Length > 0 && lods[0].renderers != null && lods[0].renderers.Length > 0)
                                ? lods[0].renderers : go.GetComponentsInChildren<Renderer>(true);
                        }
                        else rs = go.GetComponentsInChildren<Renderer>(true);
                        int tris = 0;
                        foreach (var r in rs)
                        {
                            var mf = r.GetComponent<MeshFilter>();
                            if (mf != null && mf.sharedMesh != null) tris += mf.sharedMesh.triangles.Length / 3;
                        }
                        va.meshTriCount = tris;
                        bool has = false;
                        Bounds b = default(Bounds);
                        foreach (var r in rs)
                        {
                            if (!has) { b = r.bounds; has = true; } else b.Encapsulate(r.bounds);
                        }
                        if (has)
                            va.boundsM = new BBox { min = new[] { b.min.x, b.min.y, b.min.z }, max = new[] { b.max.x, b.max.y, b.max.z } };
                        var mat = rs.Length > 0 ? rs[0].sharedMaterial : null;
                        va.shader = mat != null && mat.shader != null ? mat.shader.name : null;
                        foreach (var kv in a.textures ?? new Dictionary<string, Tex>())
                        {
                            string prop = _pipePropMap.ContainsKey(kv.Key) ? _pipePropMap[kv.Key] : null;
                            va.texturesApplied[kv.Key] = mat != null && prop != null && mat.HasProperty(prop) && mat.GetTexture(prop) != null;
                        }
                        UnityEngine.Object.DestroyImmediate(go);
                    }
                }
                doc.assets.Add(va);
            }
            doc.generatedAt = DateTimeOffset.Now.ToString("yyyy-MM-dd'T'HH:mm:sszzz");
            string outPath = Path.ChangeExtension(manifestPath, ".unity_verify.json");
            File.WriteAllText(outPath, JsonConvert.SerializeObject(doc, Formatting.Indented));
            Debug.Log("[EAP] verify → " + outPath + " assets=" + doc.assets.Count + " at=" + doc.generatedAt);
        }

        /// <summary>把 manifest 的 placements 真正落到场景：Import（材质/Prefab）→ 实例化 → 存场景。</summary>
        public static void Place(string manifestPath)
        {
            Import(manifestPath);   // 同步外部文件 + 重建材质/Prefab（Prefab 本体已带 Collider）
            var manifest = JsonConvert.DeserializeObject<Manifest>(File.ReadAllText(manifestPath));
            if (manifest == null || manifest.assets == null)
                throw new Exception("manifest 为空或结构不符: " + manifestPath);

            EnsureFolder("Assets/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var root = new GameObject(PlacementRootName);

            int total = 0, colliders = 0;
            foreach (var a in manifest.assets)
            {
                EngineTarget tgt;
                if (a.engineTargets == null || !a.engineTargets.TryGetValue("Unity", out tgt)) continue;
                var items = a.placements != null ? a.placements.items : null;
                if (items == null || items.Count == 0) continue;
                string prefabPath = PrefabAssetPath(tgt, a);
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                if (prefab == null) throw new Exception("Prefab 不存在（先跑 Import）: " + prefabPath);
                foreach (var it in items)
                {
                    var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
                    var tr = go.transform;
                    tr.SetParent(root.transform, false);
                    tr.localPosition = UeToUnityPos(it.t);
                    tr.localRotation = Quaternion.Euler(0f, UeToUnityYaw(it.r), 0f);
                    tr.localScale = (it.s != null && it.s.Length >= 3)
                        ? new Vector3(it.s[0], it.s[1], it.s[2]) : Vector3.one;
                    total++;
                    if (go.GetComponentsInChildren<Collider>(true).Length > 0) colliders++;
                }
            }
            EditorSceneManager.MarkSceneDirty(scene);
            if (!EditorSceneManager.SaveScene(scene, PlaceScenePath))
                throw new Exception("场景保存失败: " + PlaceScenePath);
            Debug.Log(string.Format("[EAP] place instances={0} colliders={1} scene={2}",
                                    total, colliders, PlaceScenePath));
        }

        /// <summary>新进程读回落位场景：实例数/包围盒/Collider/抽样材质，写 JSON 供断言。</summary>
        public static void VerifyPlacements(string manifestPath)
        {
            DetectPipeline();
            var manifest = JsonConvert.DeserializeObject<Manifest>(File.ReadAllText(manifestPath));
            if (manifest == null || manifest.assets == null)
                throw new Exception("manifest 为空或结构不符: " + manifestPath);

            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            var doc = new PlacementsVerifyDoc { manifest = Path.GetFileName(manifestPath) };
            var prefabToId = new Dictionary<string, string>();
            var byId = new Dictionary<string, List<Transform>>();
            foreach (var a in manifest.assets)
            {
                doc.instances[a.id] = 0;
                EngineTarget tgt;
                if (a.engineTargets != null && a.engineTargets.TryGetValue("Unity", out tgt))
                    prefabToId[PrefabAssetPath(tgt, a)] = a.id;
            }

            doc.sceneExists = File.Exists(Path.Combine(projectRoot, PlaceScenePath));
            if (doc.sceneExists)
            {
                var scene = EditorSceneManager.OpenScene(PlaceScenePath, OpenSceneMode.Single);
                GameObject root = null;
                foreach (var go in scene.GetRootGameObjects())
                    if (go.name == PlacementRootName) { root = go; break; }
                if (root != null)
                {
                    Vector3 min = Vector3.zero, max = Vector3.zero;
                    bool has = false;
                    int colliders = 0;
                    var sampled = new HashSet<string>();
                    foreach (Transform child in root.transform)
                    {
                        var go = child.gameObject;
                        var src = PrefabUtility.GetCorrespondingObjectFromSource(go);
                        string prefabPath = src != null ? AssetDatabase.GetAssetPath(src) : null;
                        string id;
                        if (string.IsNullOrEmpty(prefabPath) || !prefabToId.TryGetValue(prefabPath, out id))
                        { doc.unknownInstances++; continue; }
                        doc.instances[id] = doc.instances[id] + 1;
                        List<Transform> list;
                        if (!byId.TryGetValue(id, out list))
                        {
                            list = new List<Transform>();
                            byId[id] = list;
                        }
                        list.Add(child);

                        Vector3 p = child.position;
                        if (!has) { min = p; max = p; has = true; }
                        else { min = Vector3.Min(min, p); max = Vector3.Max(max, p); }

                        if (go.GetComponentsInChildren<Collider>(true).Length > 0) colliders++;

                        if (sampled.Add(id))
                        {
                            var r = go.GetComponentInChildren<Renderer>(true);
                            var mat = r != null ? r.sharedMaterial : null;
                            string prop = _pipePropMap.ContainsKey("BaseColor") ? _pipePropMap["BaseColor"] : null;
                            var tex = (mat != null && prop != null && mat.HasProperty(prop))
                                ? mat.GetTexture(prop) : null;
                            doc.sampleMaterials.Add(new SampleMaterial
                            {
                                instance = id + "[" + (doc.instances[id] - 1) + "]",
                                baseColor = tex != null ? AssetDatabase.GetAssetPath(tex) : "",
                            });
                        }
                    }
                    doc.colliders = colliders;
                    if (has)
                        doc.bboxM = new BBox
                        {
                            min = new[] { min.x, min.y, min.z },
                            max = new[] { max.x, max.y, max.z },
                        };
                }
            }
            int total = 0;
            foreach (var v in doc.instances.Values) total += v;
            doc.total = total;

            // v1.9 逐实例读回：严格按 manifest 顺序（asset 顺序 → placements.items 顺序）生成，
            // 每条都拿 manifest 的期望 transform 与实际实例逐项比对（matched=false 即顺序/落位可疑）。
            // 场景里按资产收集到的出现顺序只作为候选列表——它等于 Place() 的实例化顺序，
            // 且此处逐条校验，不会把"扫到就算"的错位静默当成 manifest 顺序。
            foreach (var a in manifest.assets)
            {
                var items = a.placements != null ? a.placements.items : null;
                if (items == null) continue;
                List<Transform> list;
                byId.TryGetValue(a.id, out list);
                for (int i = 0; i < items.Count; i++)
                {
                    var it = items[i];
                    var d = new InstanceDetail
                    {
                        assetId = a.id,
                        index = i,
                        matched = false,
                    };
                    Vector3 ep = UeToUnityPos(it.t ?? new[] { 0f, 0f, 0f });
                    float ey = UeToUnityYaw(it.r);
                    Vector3 es = (it.s != null && it.s.Length >= 3)
                        ? new Vector3(it.s[0], it.s[1], it.s[2]) : Vector3.one;
                    if (list != null && i < list.Count)
                    {
                        var tr = list[i];
                        Vector3 p = tr.position;
                        float yaw = NormalizeYaw(tr.eulerAngles.y);
                        Vector3 s = tr.lossyScale;
                        d.t = new[] { Round5(p.x), Round5(p.y), Round5(p.z) };
                        d.yawDeg = (float)Math.Round(yaw, 4);
                        d.s = new[] { Round6(s.x), Round6(s.y), Round6(s.z) };
                        d.matched = Math.Abs(p.x - ep.x) < 1e-3f && Math.Abs(p.y - ep.y) < 1e-3f
                                    && Math.Abs(p.z - ep.z) < 1e-3f
                                    && Math.Abs(Mathf.DeltaAngle(yaw, ey)) < 0.01f
                                    && Math.Abs(s.x - es.x) < 1e-4f
                                    && Math.Abs(s.y - es.y) < 1e-4f
                                    && Math.Abs(s.z - es.z) < 1e-4f;
                    }
                    doc.instancesDetail.Add(d);
                }
            }

            doc.generatedAt = DateTimeOffset.Now.ToString("yyyy-MM-dd'T'HH:mm:sszzz");
            string outPath = Path.ChangeExtension(manifestPath, ".unity_placements_verify.json");
            File.WriteAllText(outPath, JsonConvert.SerializeObject(doc, Formatting.Indented));
            Debug.Log(string.Format("[EAP] placements verify → {0} total={1} colliders={2} sceneExists={3} detail={4} at={5}",
                                    outPath, doc.total, doc.colliders, doc.sceneExists, doc.instancesDetail.Count, doc.generatedAt));
        }

        static float NormalizeYaw(float y)
        {
            y = Mathf.Repeat(y, 360f);
            if (y > 180f) y -= 360f;
            return y;
        }

        static float Round5(float v) { return (float)Math.Round(v, 5); }
        static float Round6(float v) { return (float)Math.Round(v, 6); }

        static void AddCollider(GameObject go, Asset a)
        {
            // v1.8：Unity 侧可选 collision.unity.collider（老 manifest 没有这个字段，行为不变）。
            // 选 mesh_convex 的理由：树冠不是盒状，AABB 会把树冠下方也堵死；
            // 凸包只烘一份、所有实例共享，且实例只做等比缩放（凸包支持），不需要逐实例加组件。
            string mode = a.collision?.unity?.collider ?? a.collision?.mode ?? "box";
            if (string.IsNullOrEmpty(mode) || mode == "none") return;
            var bbox = a.model?.boundingBoxM;
            if (mode == "auto_convex" || mode == "mesh_convex")
            {
                var filters = go.GetComponentsInChildren<MeshFilter>(true);
                bool added = false;
                foreach (var mf in filters)
                {
                    if (mf.sharedMesh == null) continue;
                    var mc = mf.gameObject.AddComponent<MeshCollider>();
                    mc.sharedMesh = mf.sharedMesh;
                    mc.convex = true;
                    added = true;
                }
                if (added) return;
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
