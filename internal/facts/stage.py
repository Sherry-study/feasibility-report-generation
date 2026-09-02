#!/usr/bin/env python3
"""Stage 1 (engineering facts) in-process orchestration.

Behavior contract mirrors run_skill.py stage 1 (L91-108): source registry ->
profile resolution -> resolved input manifest -> user inputs -> legacy/generic
fact build. No user-input interrupt and no chapter planning here: the
confirmation gate follows immediately; chapter plan + gap analysis + the
one-shot compilation-info questions (deferred_questions) all happen in stage 3
after confirmation. The helper functions below are copied (not moved) from
run_skill.py so the legacy orchestration stays byte-identical.
"""
from __future__ import annotations
from pathlib import Path
import yaml

from internal.common import EXIT_GENERATED, EXIT_NEEDS_RESOLUTION, dump_json, load_data
from internal.facts.builders import LEGACY_FULLSET, build_generic_facts, build_legacy_facts, normalize_facts
from internal.facts.sources import build_registry, legacy_manifest


def conservative_profile(project_id, project_name, project_type='mixed', project_level='unit'):
    return {'project_profile':{'project_id':project_id,'project_name':project_name,'project_type':project_type,'project_level':project_level,'retrofit_scope':project_level,'objectives':[],'changes':{
      'capacity_changed': None,'product_changed': None,'price_basis_changed': None,'raw_material_route_changed': None,'process_route_changed': None,'material_consumption_changed': None,'utility_demand_changed': None,'utility_system_changed': None,'equipment_changed': None,'layout_changed': None,'storage_changed': None,'outside_pipe_network_changed': None,'control_system_changed': None}}}


def resolve_profile(args, reg, resolved):
    if args.profile: return Path(args.profile).resolve()
    if args.source_manifest:
        d=load_data(args.source_manifest)
        if isinstance(d.get('project_profile'),dict):
            p=resolved/'project_profile.yaml'; p.write_text(yaml.safe_dump({'project_profile':d['project_profile']},allow_unicode=True,sort_keys=False),encoding='utf-8'); return p
    if args.workspace:
        candidates=[]
        for yp in list(Path(args.workspace).rglob('*.yaml'))+list(Path(args.workspace).rglob('*.yml')):
            try:
                d=load_data(yp)
                if isinstance(d,dict) and isinstance(d.get('project_profile'),dict): candidates.append(yp.resolve())
            except Exception: pass
        if len(candidates)==1: return candidates[0]
    # No external project_profile.yaml is required. Source Resolver has already
    # produced a source-labelled inference from selected algorithm outputs; the
    # conservative profile remains the final fallback for genuinely missing data.
    inferred=reg.get('inferred_project_profile')
    profile=inferred if isinstance(inferred,dict) else conservative_profile(
        reg['project']['project_id'], reg['project']['project_name'],
        args.project_type, args.project_level,
    )['project_profile']
    p=resolved/'project_profile.yaml'
    p.write_text(yaml.safe_dump({'project_profile':profile},allow_unicode=True,sort_keys=False),encoding='utf-8')
    return p


def discover_user_inputs(workspace):
    if not workspace: return None
    root=Path(workspace); hits=[]
    for name in ('user_inputs.yaml','user_inputs.yml','user_inputs.json','project_user_inputs.yaml','project_user_inputs.yml','project_user_inputs.json'):
        hits+=list(root.rglob(name))
    hits=list(dict.fromkeys(x.resolve() for x in hits if x.is_file()))
    return hits[0] if len(hits)==1 else None


def resolve_user_inputs(args, resolved):
    src=Path(args.user_inputs).resolve() if args.user_inputs else discover_user_inputs(args.workspace)
    data=load_data(src) if src else {}; data=data if isinstance(data,dict) else {}
    project=data.setdefault('project',{})
    if args.construction_unit is not None: project['construction_unit']=args.construction_unit
    if args.annual_operating_hours is not None: project['annual_operating_hours']=args.annual_operating_hours
    if args.project_location is not None: project['project_location']=args.project_location
    if src or any(x is not None for x in (args.construction_unit,args.annual_operating_hours,args.project_location)):
        p=resolved/'user_inputs.yaml'; p.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8'); return p
    return None


def _host_implementation_schedule(args):
    """Load host-provided implementation schedule (18.2 work-package durations, months) and validate."""
    src = getattr(args, 'implementation_schedule', None)
    if not src:
        return None
    sched = load_data(Path(src).resolve())
    if not isinstance(sched, dict):
        raise ValueError('--implementation-schedule 必须为 JSON/YAML object')
    packages = sched.get('packages')
    if not isinstance(packages, list) or not packages:
        raise ValueError('--implementation-schedule.packages 必须为非空数组')
    for pkg in packages:
        if not isinstance(pkg, dict) or not isinstance(pkg.get('name'), str) or not pkg.get('name', '').strip():
            raise ValueError('--implementation-schedule.packages 每项必须包含非空 name')
        dur = pkg.get('duration_months')
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur <= 0:
            raise ValueError(f"工作包 {pkg.get('name')} 的 duration_months 必须为正数")
    total = sched.get('total_duration_months')
    if total is not None and (not isinstance(total, (int, float)) or isinstance(total, bool) or total <= 0):
        raise ValueError('--implementation-schedule.total_duration_months 必须为正数')
    return {**sched, 'status': 'host_provided'}


def _facts_brief(fdata):
    """从完整 project facts 提取精简摘要，避免返回值携带全量流股组成等大对象。"""
    project = fdata.get('project') or {}
    process = fdata.get('process') or {}
    design = process.get('design') or {}
    retrofit = process.get('retrofit') or {}
    equipment = fdata.get('equipment') or {}
    annual_capacity = (fdata.get('fa') or {}).get('annual_capacity') or {}

    def _stream_brief(streams):
        return [
            {k: s.get(k) for k in ('stream_id', 'name', 'flow_kg_h', 'flow_unit', 'phase', 'target_product_stream') if k in s}
            for s in streams or []
        ]

    return {
        'project_name': project.get('project_name'),
        'project_id': project.get('project_id'),
        'project_level': project.get('project_level'),
        'project_type': project.get('project_type'),
        'construction_unit': project.get('construction_unit'),
        'project_location': project.get('project_location'),
        'annual_operating_hours': (fdata.get('user') or {}).get('annual_operating_hours'),
        'adopted_scheme': (fdata.get('adopted_scheme') or {}).get('scheme_name')
                          or (fdata.get('adopted_scheme') or {}).get('status'),
        'process': {
            'design': {
                'stream_count': len(design.get('streams') or []),
                'feeds': _stream_brief(design.get('external_feeds')),
                'products': _stream_brief(design.get('product_streams')),
                'separator_count': len(design.get('separators') or []),
                'reactor_count': len(design.get('reactors') or []),
            },
            'retrofit': {
                'stream_count': len(retrofit.get('streams') or []),
                'feeds': _stream_brief(retrofit.get('external_feeds')),
                'products': _stream_brief(retrofit.get('product_streams')),
                'separator_count': len(retrofit.get('separators') or []),
                'reactor_count': len(retrofit.get('reactors') or []),
            },
        },
        'equipment': {
            'object_count': len(equipment.get('object_catalog') or []),
            'tower_rows': len((equipment.get('tower') or {}).get('report_rows') or []),
            'reactor_rows': len((equipment.get('reactor') or {}).get('report_rows') or []),
        },
        'gaps': [g.get('field') for g in (fdata.get('gaps') or [])],
        'annual_capacity': {
            'status': annual_capacity.get('status'),
            'design_t_a': annual_capacity.get('design_t_a'),
            'retrofit_t_a': annual_capacity.get('retrofit_t_a'),
        },
    }


def run_engineering_facts_stage(args, output_dir):
    """Run stage 1. Returns (exit_code, summary_dict) without printing."""
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True); resolved=out/'_resolved'; resolved.mkdir(exist_ok=True)

    reg=build_registry(Path(args.workspace).resolve() if args.workspace else None,Path(args.source_manifest).resolve() if args.source_manifest else None,args.project_name,args.project_id,args.project_level,args.project_type)
    dump_json(reg,resolved/'source_registry.json')
    if not reg['ready_for_fact_resolution']:
        return EXIT_NEEDS_RESOLUTION, {'status':'needs_resolution','source_registry':str(resolved/'source_registry.json'),'missing':reg.get('missing_required_sources',[]),'ambiguities':reg['ambiguities']}
    profile=resolve_profile(args,reg,resolved); profile_data=load_data(profile); profile_obj=profile_data.get('project_profile',profile_data)
    user_inputs=resolve_user_inputs(args,resolved)
    # User-provided project name may refine an algorithm-inferred/workspace
    # placeholder, but never overrides an explicit profile or CLI metadata.
    if user_inputs and profile_obj.get('profile_provenance',{}).get('mode')=='algorithm_inferred':
        ui=load_data(user_inputs) or {}
        ui_name=(ui.get('project') or {}).get('project_name') if isinstance(ui,dict) else None
        if ui_name:
            profile_obj=dict(profile_obj); profile_obj['project_name']=ui_name; profile_obj['project_name_source']='user_input'
            profile.write_text(yaml.safe_dump({'project_profile':profile_obj},allow_unicode=True,sort_keys=False),encoding='utf-8')
    manifest_path=resolved/'resolved_input_manifest.yaml'
    manifest_path.write_text(yaml.safe_dump(legacy_manifest(reg,profile_obj),allow_unicode=True,sort_keys=False),encoding='utf-8')

    facts=out/'project_facts.json'
    manifest_data=load_data(manifest_path)
    source_types={((s or {}).get('source_type') or sid) for sid,s in (manifest_data.get('sources') or {}).items()}
    input_dir=args.workspace or str(Path(args.source_manifest).resolve().parent)
    if LEGACY_FULLSET.issubset(source_types):
        fdata=build_legacy_facts(input_dir,manifest_path,user_inputs); adapter_mode='legacy_fullstack'
    else:
        fdata=build_generic_facts(input_dir,manifest_data,load_data(user_inputs) if user_inputs else {}); adapter_mode='generic_partial'
    fdata=normalize_facts(fdata); fdata['meta']['adapter_mode']=adapter_mode
    # 记录 project_name 来源（explicit/workspace_dir/default），供阶段③ deferred 提问检测
    # “项目名仅为数据源目录名占位”的情况（避免报告封面出现目录名而无任何提示）。
    # 若 Adapter 已按 user_inputs 将项目名升级为用户输入（project_name_source=user_input），保留该来源，不再回退 registry 占位值。
    if fdata.setdefault('project',{}).get('project_name_source')!='user_input':
        fdata['project']['project_name_source']=reg['project'].get('project_name_source') or 'default'
    host_schedule=_host_implementation_schedule(args)
    if host_schedule: fdata['implementation_schedule']=host_schedule
    dump_json(fdata,facts)

    # 章节规划与缺口分析不在本阶段执行：确认 Gate 紧邻事实整理，章节计划、
    # 缺口分析与编制信息一次性提问（deferred_questions）统一延后到阶段③（确认后）。
    return EXIT_GENERATED, {'status':'facts_ready','resume_exit_code':EXIT_GENERATED,'source_registry':str(resolved/'source_registry.json'),'resolved_input_manifest':str(manifest_path),'project_profile':str(profile),'user_inputs':str(user_inputs) if user_inputs else None,'facts':str(facts),'adapter_mode':adapter_mode,'project_facts_summary':_facts_brief(fdata)}
