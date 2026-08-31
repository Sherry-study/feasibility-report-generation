#!/usr/bin/env python3
"""Source discovery and source-capability resolver for feasibility-report projects.

V0.11 principle:
- concrete files/source signatures are Adapter concerns;
- the Skill consumes normalized capabilities / Project Facts;
- no case-specific source type is globally REQUIRED.
"""
from __future__ import annotations
import argparse, json, re, sys
from copy import deepcopy
from dataclasses import dataclass, asdict
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.common import load_data, load_json_loose, dump_json

KNOWN_SOURCE_TYPES = [
    "plant_level", "plant_diagnosis", "retrofit_topology", "retrofit_equipment",
    "new_device_params", "tower_result", "reactor_result", "candidate_scheme", "plant_info"
]
ROLE_BY_TYPE = {
    "plant_level":"PA", "plant_diagnosis":"PA", "retrofit_topology":"PA",
    "retrofit_equipment":"PA", "new_device_params":"PA",
    "tower_result":"EA", "reactor_result":"EA", "candidate_scheme":"PA", "plant_info":"PA",
    "project_document":"U", "project_profile":"META", "source_manifest":"META",
}
CAPABILITIES_BY_TYPE = {
    "plant_level":["process_case_result", "material_balance", "process_streams"],
    "plant_diagnosis":["diagnosis_result"],
    "retrofit_topology":["adopted_topology", "process_topology"],
    "retrofit_equipment":["equipment_catalog"],
    "new_device_params":["equipment_design_parameters"],
    "tower_result":["equipment_result", "separation_equipment_result"],
    "reactor_result":["equipment_result", "reaction_equipment_result"],
    "candidate_scheme":["scheme_candidate_result"],
    "plant_info":["project_operating_basis"],
    "project_document":["project_document"],
    "project_profile":["project_profile"],
}
SKIP_DIRS={".git","node_modules","__pycache__","outputs","output","dist","build","tests","test","fixtures","qa_render"}

PROFILE_CHANGE_KEYS = (
    "capacity_changed", "product_changed", "price_basis_changed",
    "raw_material_route_changed", "process_route_changed",
    "material_consumption_changed", "utility_demand_changed",
    "utility_system_changed", "equipment_changed", "layout_changed",
    "storage_changed", "outside_pipe_network_changed",
    "control_system_changed",
)

@dataclass
class Asset:
    path: str
    source_type: str
    role: str
    confidence: float
    detector: str
    version_hint: str|None=None
    status: str="candidate"
    notes: str|None=None
    capabilities: list[str]|None=None


def _version_hint(name:str):
    m=re.search(r"(?:^|[_\- ])v(\d+(?:\.\d+)*)", name, re.I)
    return ("v"+m.group(1)) if m else None


def _asset(path, source_type, role, confidence, detector, version=None, notes=None):
    return Asset(str(path), source_type, role, confidence, detector, version, "candidate", notes, CAPABILITIES_BY_TYPE.get(source_type, []))


def classify(path:Path)->Asset|None:
    ext=path.suffix.lower(); name=path.name.lower()
    annotated=any(x in name for x in ("annotated","注释","commented"))
    if ext in {".json",".jsonc"}:
        try: d=load_json_loose(path)
        except Exception: return _asset(path,"unparsed_json","UNKNOWN",0.2,"json_parse_failed",notes="JSON/JSONC无法解析")
        if not isinstance(d,dict): return _asset(path,"other_data","UNKNOWN",0.2,"json_non_object")
        k=set(d.keys())
        if isinstance(d.get("plant_info"), dict) and d.get("plant_info",{}).get("annual_operating_hours") not in (None, ""):
            return _asset(path,"plant_info","PA",0.99,"json_signature",_version_hint(path.name),"装置基础信息/年运行时长")
        if {"design_case","retrofit_case","components"}.issubset(k):
            return _asset(path,"plant_level","PA",0.99,"json_signature",_version_hint(path.name),"工艺设计/改造工况结构签名")
        if d.get("schemaVersion") == "topology_retrofit_v1" and d.get("schemeKind") == "diagnosis" and isinstance(d.get("candidates"), list):
            return _asset(path,"candidate_scheme","PA",0.99,"json_signature",_version_hint(path.name),"候选方案结果；candidate不等同最终采用方案")
        if {"nodes","edges","schemeInfo"}.issubset(k):
            return _asset(path,"retrofit_topology","PA",0.99,"json_signature",_version_hint(path.name))
        if "separator" in k and "combined_schemes" in k:
            return _asset(path,"tower_result","EA",0.99 if not annotated else 0.55,"json_signature",_version_hint(path.name),"annotated候选仅作低优先级" if annotated else None)
        if "reactors" in k and "combined_schemes" in k:
            return _asset(path,"reactor_result","EA",0.99 if not annotated else 0.55,"json_signature",_version_hint(path.name),"annotated候选仅作低优先级" if annotated else None)
        if "reactors" in k and "separators" in k and "combined_schemes" not in k:
            return _asset(path,"new_device_params","PA",0.95,"json_signature",_version_hint(path.name))
        if "equipment" in k and isinstance(d.get("equipment"),list):
            return _asset(path,"retrofit_equipment","PA",0.95,"json_signature",_version_hint(path.name))
        return _asset(path,"other_data","UNKNOWN",0.25,"json_unknown")
    if ext in {".md",".txt"}:
        txt=path.read_text(encoding="utf-8-sig", errors="replace")[:20000]
        diagnostic_name=any(x in name for x in ("diagnosis","diagnostic","诊断"))
        diagnostic_body=txt.lstrip().startswith("# 工艺诊断报告") or ("## 一、装置总体指标" in txt and "## 三、关键瓶颈分析" in txt)
        if diagnostic_name or diagnostic_body:
            return _asset(path,"plant_diagnosis","PA",0.95 if diagnostic_body else 0.82,"text_signature",_version_hint(path.name))
        return _asset(path,"text_document","U",0.35,"generic_text")
    if ext in {".doc",".docx",".pdf",".xls",".xlsx",".csv",".ppt",".pptx"}:
        return _asset(path,"project_document","U",0.30,"generic_document",notes="已发现；需文档Adapter抽取后才能形成Project Facts")
    if ext in {".yaml",".yml"}:
        try: d=yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        except Exception: d=None
        if isinstance(d,dict) and "project_profile" in d:
            return _asset(path,"project_profile","META",0.99,"yaml_signature")
        if isinstance(d,dict) and "sources" in d:
            return _asset(path,"source_manifest","META",0.90,"yaml_signature")
    return None


def scan_workspace(root:Path):
    assets=[]
    for p in root.rglob("*"):
        if not p.is_file(): continue
        if any(part in SKIP_DIRS or part.startswith('.') for part in p.relative_to(root).parts[:-1]): continue
        a=classify(p)
        if a: assets.append(a)
    return assets


def load_explicit_manifest(path:Path):
    d=load_data(path)
    base=path.parent
    srcs=d.get("sources",{})
    if isinstance(srcs,list): srcs={x.get("id") or x.get("source_id"):x for x in srcs}
    normalized={}
    for sid,s in (srcs or {}).items():
        if not sid or not isinstance(s,dict): continue
        loc=s.get("locator") or {}
        ltype=loc.get("type") or ("local_path" if s.get("file") or s.get("path") else None)
        val=loc.get("value") or s.get("file") or s.get("path")
        if ltype!="local_path":
            raise ValueError(f"source {sid}: locator.type={ltype!r} 当前未实现；可由宿主Resolver扩展")
        p=Path(val)
        if not p.is_absolute(): p=(base/p).resolve()
        declared_type=s.get("source_type") or sid
        normalized[sid]={**s,"path":str(p),"source_type":declared_type,"role":s.get("role") or ROLE_BY_TYPE.get(declared_type,"UNKNOWN")}
    return d, normalized


def choose_auto(assets):
    by={}
    for a in assets:
        if a.source_type in KNOWN_SOURCE_TYPES: by.setdefault(a.source_type,[]).append(a)
    selected={}; ambiguities=[]
    for st,candidates in by.items():
        cands=sorted(candidates,key=lambda x:x.confidence,reverse=True)
        high=[x for x in cands if x.confidence>=0.8]
        if len(high)>1:
            ambiguities.append({"source_type":st,"required":False,"candidates":[asdict(x) for x in high]})
            continue
        best=cands[0]; best.status="selected"; selected[st]=best
    return selected, ambiguities


def _capability_coverage(selected):
    coverage={}
    for sid,a in selected.items():
        for cap in a.capabilities or CAPABILITIES_BY_TYPE.get(a.source_type,[]):
            coverage.setdefault(cap,[]).append(sid)
    return coverage


def _load_selected_payloads(reg):
    """Load only selected algorithm outputs for profile inference.

    Profile inference is deliberately best-effort. A malformed or unsupported
    source is ignored here and remains visible to the normal Adapter/Fact
    validation path; this function must never turn a source parse error into a
    fabricated project profile.
    """
    payloads={}
    inferable_types={"plant_info", "plant_level", "retrofit_topology"}
    for sid, asset in (reg.get("selected_sources") or {}).items():
        source_type=(asset or {}).get("source_type") or sid
        if source_type not in inferable_types:
            continue
        path=(asset or {}).get("path")
        if not path:
            continue
        try:
            p=Path(path)
            if p.suffix.lower() in {".json", ".jsonc", ".yaml", ".yml"}:
                value=load_data(p)
            else:
                continue
        except Exception:
            continue
        if isinstance(value, dict):
            payloads.setdefault(source_type, []).append((str(p), value))
    return payloads


def _first_text(payload, keys, max_depth=4):
    """Find a clearly named project/plant title without guessing from prose."""
    wanted={str(x).lower() for x in keys}
    stack=[(payload, 0)]
    while stack:
        current, depth=stack.pop()
        if depth > max_depth:
            continue
        if isinstance(current, dict):
            for key, value in current.items():
                if str(key).strip().lower() in wanted and isinstance(value, str):
                    text=value.strip()
                    if 2 <= len(text) <= 120:
                        return text
                if isinstance(value, (dict, list)):
                    stack.append((value, depth + 1))
        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    stack.append((value, depth + 1))
    return None


def _case_product(case):
    if not isinstance(case, dict):
        return None, None
    products=case.get("products") or []
    if isinstance(products, dict):
        products=[products]
    if not isinstance(products, list):
        return None, None
    ordered=sorted(products, key=lambda x: 0 if isinstance(x, dict) and x.get("is_main_product") else 1)
    for product in ordered:
        if not isinstance(product, dict):
            continue
        name=product.get("name") or product.get("product_name")
        cap=product.get("annual_capacity")
        value=cap.get("value") if isinstance(cap, dict) else cap
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return name, float(value)
    return None, None


def _capacity_pair(payloads):
    """Return (design_name, design_capacity, retrofit_name, retrofit_capacity)."""
    for _, payload in payloads:
        if isinstance(payload.get("plant_info"), dict):
            payload=payload["plant_info"]
        design=payload.get("design_case")
        retrofit=payload.get("retrofit_case")
        dn, dc=_case_product(design)
        rn, rc=_case_product(retrofit)
        if dc is not None or rc is not None:
            return dn, dc, rn, rc
    return None, None, None, None


def _topology_change(payloads):
    """Infer route/equipment change only from explicit topology deltas."""
    saw_equipment=False
    saw_route=False
    for _, payload in payloads:
        scheme_info=payload.get("schemeInfo")
        if isinstance(scheme_info, list):
            for item in scheme_info:
                if not isinstance(item, dict):
                    continue
                keys=("newEquipment", "modifiedEquipment", "removedEquipment",
                      "newEquipment_list", "modifiedEdges", "newEdges", "removedEdges",
                      "modifiedStream_list", "newStream_list", "removedStream_list")
                if any(item.get(key) not in (None, [], {}) for key in keys):
                    saw_equipment=True
                    saw_route = saw_route or any(item.get(key) not in (None, [], {}) for key in
                                                 ("modifiedEdges", "newEdges", "removedEdges",
                                                  "modifiedStream_list", "newStream_list", "removedStream_list"))
        design=(payload.get("design_case") or {}).get("topology")
        retrofit=(payload.get("retrofit_case") or {}).get("topology")
        if isinstance(design, dict) and isinstance(retrofit, dict) and design != retrofit:
            saw_equipment=True
            saw_route=True
    return (True, saw_route) if saw_equipment else (None, None)


def infer_project_profile(reg, project_type="mixed", project_level="unit"):
    """Build a source-labelled profile from algorithm outputs when no profile file is supplied.

    Explicit CLI/manifest metadata remains authoritative. Only high-confidence
    facts with unambiguous source signatures are promoted; unresolved fields
    retain the conservative defaults and remain available for Confirmation Gate.
    """
    project=reg.get("project") or {}
    inferred={
        "project_id": project.get("project_id") or "PROJECT",
        "project_name": project.get("project_name") or "待确认项目",
        "project_level": project.get("project_level") or project_level or "unit",
        "project_type": project.get("project_type") or project_type or "mixed",
        "retrofit_scope": project.get("project_level") or project_level or "unit",
        "objectives": [],
        "changes": {key: None for key in PROFILE_CHANGE_KEYS},
        "profile_provenance": {"mode": "algorithm_inferred", "fields": {}},
    }
    payloads=_load_selected_payloads(reg)

    if project.get("project_name_source") not in ("explicit", "manifest"):
        for source_type in ("plant_info", "plant_level"):
            for source_path, payload in payloads.get(source_type, []):
                name=_first_text(payload, ("project_name", "projectName", "plant_name", "plantName"))
                if name:
                    inferred["project_name"]=name
                    inferred["project_name_source"]="algorithm_output"
                    inferred["profile_provenance"]["fields"]["project_name"]={"source_paths":[source_path],"confidence":0.95}
                    break
            if inferred.get("project_name_source")=="algorithm_output":
                break

    all_payloads=[item for values in payloads.values() for item in values]
    dn, dc, rn, rc=_capacity_pair(all_payloads)
    if dc is not None and rc is not None and dc != rc:
        changed=True
        inferred["changes"]["capacity_changed"]=changed
        inferred["profile_provenance"]["fields"]["capacity_changed"]={"source_paths":["algorithm_output.design_case/retrofit_case.products"],"confidence":0.98}
        if project.get("project_type_source") not in ("explicit", "manifest") and rc > dc:
            inferred["project_type"]="capacity_expansion"
            inferred["retrofit_scope"]="扩能改造"
            inferred["objectives"].append(f"将{rn or dn or '主要产品'}年产能由{dc:g}提升至{rc:g}。")
        elif project.get("project_type_source") not in ("explicit", "manifest"):
            inferred["project_type"]="capacity_change"
            inferred["retrofit_scope"]="产能调整"
        elif rc > dc:
            inferred["objectives"].append(f"将{rn or dn or '主要产品'}年产能由{dc:g}提升至{rc:g}。")
        if dn and rn and dn != rn:
            inferred["changes"]["product_changed"]=True
            inferred["profile_provenance"]["fields"]["product_changed"]={"source_paths":["algorithm_output.design_case/retrofit_case.products"],"confidence":0.95}

    equipment_changed, route_changed=_topology_change(payloads.get("retrofit_topology", []) + payloads.get("plant_level", []))
    if equipment_changed is not None:
        inferred["changes"]["equipment_changed"]=equipment_changed
        inferred["profile_provenance"]["fields"]["equipment_changed"]={"source_paths":["algorithm_output.schemeInfo/topology"],"confidence":0.9}
    if route_changed is not None:
        inferred["changes"]["process_route_changed"]=route_changed
        inferred["profile_provenance"]["fields"]["process_route_changed"]={"source_paths":["algorithm_output.schemeInfo/topology"],"confidence":0.9}

    if not inferred["objectives"]:
        inferred["objectives"]=[]
    return inferred


def build_registry(workspace:Path|None, manifest:Path|None, project_name=None, project_id=None, project_level="unit", project_type="mixed"):
    assets=scan_workspace(workspace) if workspace else []
    explicit_meta={}; selected={}; ambiguities=[]
    if manifest:
        explicit_meta, exp=load_explicit_manifest(manifest)
        for sid,s in exp.items():
            p=Path(s["path"])
            if not p.exists(): raise FileNotFoundError(f"显式来源不存在: {sid}: {p}")
            declared=s.get("source_type") or sid
            probe=classify(p)
            note=None
            if probe and declared in KNOWN_SOURCE_TYPES and probe.source_type not in {declared,"other_data","unparsed_json","project_document","text_document"}:
                note=f"显式声明为{declared}，内容签名识别为{probe.source_type}；需人工复核"
            caps=s.get("capabilities") or CAPABILITIES_BY_TYPE.get(declared,[])
            selected[sid]=Asset(str(p),declared,s.get("role") or ROLE_BY_TYPE.get(declared,"UNKNOWN"),1.0,"explicit_manifest",s.get("version"),"selected",note,list(caps))
        if workspace:
            auto, auto_amb=choose_auto(assets)
            declared_types={a.source_type for a in selected.values()}
            for sid,a in auto.items():
                if a.source_type not in declared_types: selected[sid]=a
            ambiguities=[x for x in auto_amb if x["source_type"] not in declared_types]
    else:
        selected, ambiguities=choose_auto(assets)
    pn=project_name or explicit_meta.get("project_name") or (explicit_meta.get("project") or {}).get("project_name")
    pn_source="explicit" if pn else None
    pid=project_id or explicit_meta.get("project_id") or (explicit_meta.get("project") or {}).get("project_id")
    if not pn and workspace: pn=workspace.name; pn_source="workspace_dir"
    if not pn: pn_source="default"
    # project_id 必须跨运行稳定：锚定到数据源目录名，而非展示用 project_name
    # （中文项目名经清洗会塌缩为 "PROJECT"，导致重跑时 research_evidence/section_drafts
    #   的 project_id 校验失败）。
    if not pid: pid=re.sub(r"[^A-Za-z0-9_-]+","-",(workspace.name if workspace else None) or "PROJECT").strip('-') or "PROJECT"
    project_meta=(explicit_meta.get("project") or {}) if isinstance(explicit_meta,dict) else {}
    profile=(explicit_meta.get("project_profile") or {}) if isinstance(explicit_meta,dict) else {}
    level=profile.get("project_level") or project_meta.get("project_level") or project_level or "unit"
    ptype=profile.get("project_type") or project_meta.get("project_type") or project_type or "mixed"
    coverage=_capability_coverage(selected)
    project_type_source=("manifest" if profile.get("project_type") or project_meta.get("project_type") else
                         ("explicit" if project_type not in (None, "mixed") else None))
    registry={
        "contract_version":"1.1",
        "project":{"project_id":pid,"project_name":pn or "待确认项目",
                   "project_name_source":pn_source,
                   "project_type_source":project_type_source,
                   "project_level":level,"project_type":ptype,
                   "construction_unit":project_meta.get("construction_unit"),
                   "project_location":project_meta.get("project_location"),
                   "annual_operating_hours":project_meta.get("annual_operating_hours")},
        "mode":"hybrid" if manifest and workspace else ("explicit" if manifest else "workspace_auto"),
        "workspace":str(workspace.resolve()) if workspace else None,
        "manifest":str(manifest.resolve()) if manifest else None,
        "assets":[asdict(x) for x in assets],
        "selected_sources":{k:asdict(v) for k,v in selected.items()},
        "capability_coverage":coverage,
        "missing_required_sources":[],
        "ambiguities":ambiguities,
        "ready_for_fact_resolution":not ambiguities,
        "ready_for_current_adapter":not ambiguities,
        "limitations":[
            "Source Resolver只负责发现、版本歧义和能力登记，不再把某一案例的文件类型设为全局必需输入。",
            "未知算法输出只登记、不猜测业务语义；由Adapter映射到Project Facts Contract。",
            "DOC/DOCX/PDF/XLSX等项目资料当前仅登记，需UserDocumentAdapter后续抽取。",
            "MCP/数据中心locator由宿主Resolver扩展，Project Facts Contract不随存储方式变化。"
        ]
    }
    inferred=infer_project_profile(registry, project_type=ptype, project_level=level)
    registry["inferred_project_profile"]=inferred
    if inferred.get("project_name_source")=="algorithm_output" and registry["project"].get("project_name_source") not in ("explicit", "manifest"):
        registry["project"]["project_name"]=inferred["project_name"]
        registry["project"]["project_name_source"]="algorithm_output"
    return registry


def legacy_manifest(reg, project_profile=None):
    src={}
    for sid,a in reg["selected_sources"].items():
        src[sid]={"source_type":a.get("source_type") or sid,"role":a["role"],"file":a["path"],"version":a.get("version_hint") or "current","capabilities":a.get("capabilities") or [],"description":f"Source Resolver: {a['detector']}"}
    profile=project_profile or reg.get("inferred_project_profile") or {
        "project_id":reg["project"]["project_id"],"project_name":reg["project"]["project_name"],
        "project_level":reg["project"].get("project_level","unit"),"project_type":reg["project"].get("project_type","mixed"),
        "changes":{}
    }
    project=dict(reg.get("project") or {})
    for key in ("project_id", "project_name", "project_level", "project_type"):
        if profile.get(key) not in (None, ""):
            project[key]=profile[key]
    project_name=profile.get("project_name") or reg["project"].get("project_name")
    project_id=profile.get("project_id") or reg["project"].get("project_id")
    return {
        "project_id":project_id,"project_name":project_name,
        "project":project,"project_profile":profile,
        "skill_version":"0.14.8","baseline_status":"resolved","sources":src,
        "excluded_sources":[],"user_inputs_required":{}
    }


def main():
    ap=argparse.ArgumentParser(description="Resolve project sources into source capabilities")
    ap.add_argument("--workspace"); ap.add_argument("--source-manifest")
    ap.add_argument("--project-name"); ap.add_argument("--project-id")
    ap.add_argument("--project-level",choices=["equipment","unit","system","plant"],default="unit")
    ap.add_argument("--project-type",default="mixed")
    ap.add_argument("--output-dir",required=True)
    args=ap.parse_args()
    if not args.workspace and not args.source_manifest: ap.error("至少提供 --workspace 或 --source-manifest")
    workspace=Path(args.workspace).resolve() if args.workspace else None
    manifest=Path(args.source_manifest).resolve() if args.source_manifest else None
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    reg=build_registry(workspace,manifest,args.project_name,args.project_id,args.project_level,args.project_type)
    dump_json(reg,out/"source_registry.json")
    (out/"resolved_input_manifest.yaml").write_text(yaml.safe_dump(legacy_manifest(reg),allow_unicode=True,sort_keys=False),encoding="utf-8")
    summary={"ready":reg["ready_for_fact_resolution"],"selected":list(reg["selected_sources"]),"capabilities":sorted(reg["capability_coverage"]),"ambiguities":[x["source_type"] for x in reg["ambiguities"]],"registry":str(out/"source_registry.json")}
    print(json.dumps(summary,ensure_ascii=False))
    return 0 if reg["ready_for_fact_resolution"] else 2

if __name__=="__main__": sys.exit(main())


# ===== 候选方案 Adapter 区（原候选方案适配器模块已并入本文件） =====
# Adapter for PA candidate-scheme outputs with schemaVersion=topology_retrofit_v1.
#
# Important semantic boundary:
# - this output belongs to the candidate/diagnosis stage;
# - recommendation means algorithm recommendation at candidate stage;
# - the candidate pool defines stable scheme identities for report comparison;
# - later optimization is an iteration within the same scheme identity and does not create
#   an additional report candidate;
# - the candidate-stage recommendation MUST NOT be promoted to the final adopted scheme
#   without a later explicit/frozen engineering source.

def _eval_item(obj):
    if not isinstance(obj, dict):
        return None
    return {
        "level": obj.get("level"),
        "comment": obj.get("comment"),
        "key_basis": obj.get("keyBasis"),
        "key_metrics": obj.get("keyMetrics"),
        "pending_items": obj.get("pendingItems"),
    }


def adapt_candidate_scheme(data: dict, source_file: str | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("candidate scheme source must be a JSON object")
    if data.get("schemaVersion") != "topology_retrofit_v1" or data.get("schemeKind") != "diagnosis":
        raise ValueError("unsupported candidate scheme schema/signature")

    diagnosis=[]
    for issue in data.get("issues", []) or []:
        d=issue.get("diagnosis") or {}
        diagnosis.append({
            "issue_id": issue.get("issueId"),
            "constraint_component": d.get("constraintComponent"),
            "associated_component": d.get("associatedComponent"),
            "boiling_point_difference": d.get("boilingPointDiff"),
            "bottleneck_equipment": d.get("bottleneckEquipment"),
            "current_topology_pattern": d.get("currentTopologyPattern"),
            "current_topology_pattern_label": d.get("currentTopologyPatternLabel"),
            "bottleneck_type": d.get("bottleneckType"),
            "bottleneck_type_label": d.get("bottleneckTypeLabel"),
            "recommended_retrofit_pattern": d.get("recommendedRetrofitPattern"),
            "recommended_retrofit_pattern_label": d.get("recommendedRetrofitPatternLabel"),
            "basis": d.get("basis"),
            "assumptions": d.get("assumptions"),
            "risks": d.get("risks"),
        })

    candidates=[]
    for c in data.get("candidates", []) or []:
        ev=c.get("evaluation") or {}
        candidates.append({
            "scheme_id": c.get("schemeId") or c.get("scheme_id") or c.get("id"),
            "name": c.get("name"),
            "brief": c.get("brief"),
            "retrofit_type": c.get("retrofitType"),
            "retrofit_type_label": c.get("retrofitTypeLabel"),
            "description": c.get("description"),
            "new_equipment": deepcopy(c.get("newEquipment_list") or []),
            "removed_equipment": deepcopy(c.get("removedEquipment_list") or []),
            "new_streams": deepcopy(c.get("newStream_list") or []),
            "modified_streams": deepcopy(c.get("modifiedStream_list") or []),
            "removed_streams": deepcopy(c.get("removedStream_list") or []),
            "outlet_disposition": deepcopy(c.get("outletDisposition") or []),
            "resulting_topology_pattern": c.get("resultingTopologyPattern"),
            "resulting_topology_pattern_label": c.get("resultingTopologyPatternLabel"),
            "expected_effect": c.get("expectedEffect"),
            "scheme_details": c.get("schemeDetails"),
            "evaluation": {
                "technical_feasibility": _eval_item(ev.get("technicalFeasibility")),
                "implementation_complexity": _eval_item(ev.get("implementationComplexity")),
                "operational_risk": _eval_item(ev.get("operationalRisk")),
            },
            "score": c.get("score"),
            "_meta": {
                "source_type": "PA",
                "capability": "candidate_scheme_generation",
                "stage": "candidate",
                "source_file": source_file,
                "approval_status": "candidate_output",
            },
        })

    rec=data.get("recommendation") or {}
    return {
        "schema_version": data.get("schemaVersion"),
        "scheme_kind": data.get("schemeKind"),
        "stage": "candidate",
        "diagnosis": diagnosis,
        "candidates": candidates,
        "algorithm_recommendation": {
            "combination": deepcopy(rec.get("combination") or []),
            "reason": rec.get("reason"),
            "authority": "candidate_stage_algorithm_recommendation_only",
            "is_final_selected_scheme": False,
        },
        "final_selection": {
            "status": "not_inferred_from_candidate_output",
            "rule": "最终采用方案身份来自首次候选池，但最终采用与否由后续工程化/冻结结果确定；后续优化不新增候选方案身份，最终方案描述以最终拓扑及最终设备事实为准。",
        },
        "_meta": {
            "source_type": "PA",
            "capability": "candidate_scheme_generation",
            "stage": "candidate",
            "source_file": source_file,
            "candidate_pool_semantics": "first_recommendation_pool_is_authoritative_for_report_comparison; optimization_iterations_do_not_add_report_candidates",
        },
    }
