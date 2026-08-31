#!/usr/bin/env python3
"""Report-stage process topology analysis: main path, detours, recycles and retrofit changes."""
from __future__ import annotations
import sys
from collections import defaultdict,deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

PASSIVE_TYPES={'tee','mixer'}

def largest_stream(items):
    return max(items or [],key=lambda x: float(x.get('flow_kg_h') or 0),default=None)

def shortest_path(nodes,edges,start,target):
    adj=defaultdict(list)
    for e in edges:
        s=str(e.get('source') or ''); t=str(e.get('target') or '')
        if s and t: adj[s].append(t)
    q=deque([(start,[start])]); seen={start}
    while q:
        n,p=q.popleft()
        if n==target: return p
        for nxt in adj[n]:
            if nxt not in seen:
                seen.add(nxt); q.append((nxt,p+[nxt]))
    return []

def enumerate_detours(edges,main_path,max_depth=10):
    adj=defaultdict(list); main=set(main_path); idx={n:i for i,n in enumerate(main_path)}
    for e in edges:
        s=str(e.get('source') or ''); t=str(e.get('target') or '')
        if s and t: adj[s].append((t,str(e.get('id') or '')))
    detours=[]; seen_paths=set()
    def walk(origin,node,path,edge_ids):
        if len(path)>max_depth: return
        if node in main and node!=origin:
            key=tuple(path)
            if key not in seen_paths:
                seen_paths.add(key); detours.append({'from_main_node':origin,'to_main_node':node,'node_ids':path,'edge_ids':edge_ids,'returns_backward':idx.get(node,999)<=idx.get(origin,-1)})
            return
        for nxt,eid in adj.get(node,[]):
            if nxt in path: continue
            walk(origin,nxt,path+[nxt],edge_ids+[eid])
    for origin in main_path:
        for nxt,eid in adj.get(origin,[]):
            if nxt in main:
                continue
            walk(origin,nxt,[origin,nxt],[eid])
    return detours

def analyze(facts):
    design=((facts.get('process') or {}).get('design') or {}); retrofit=((facts.get('process') or {}).get('retrofit') or {})
    top=retrofit.get('topology') or {}; nodes=top.get('nodes') or []; edges=top.get('edges') or []
    node_map={str(n.get('id')):n for n in nodes}
    feed=largest_stream(retrofit.get('external_feeds') or [])
    product=largest_stream(retrofit.get('product_streams') or [])
    start=str((feed or {}).get('target_equipment_id') or '')
    target=str((product or {}).get('source_equipment_id') or '')
    main_ids=shortest_path(nodes,edges,start,target) if start and target else []
    main_units=[node_map[x] for x in main_ids if x in node_map]
    main_process=[n for n in main_units if n.get('type') not in PASSIVE_TYPES]
    idx={n:i for i,n in enumerate(main_ids)}
    recycle=[]
    for e in edges:
        s=str(e.get('source') or ''); t=str(e.get('target') or '')
        if s in idx and t in idx and idx[t] <= idx[s]:
            recycle.append({'stream_id':e.get('id'),'from_id':s,'from':node_map.get(s,{}).get('name'),'to_id':t,'to':node_map.get(t,{}).get('name')})
    detours=enumerate_detours(edges,main_ids)
    for d in detours:
        d['units']=[{'id':x,'name':node_map.get(x,{}).get('name'),'type':node_map.get(x,{}).get('type')} for x in d['node_ids']]
    design_top=design.get('topology') or {}; dn={str(n.get('id')):n for n in design_top.get('nodes') or []}; de={str(e.get('id')):e for e in design_top.get('edges') or []}
    rn={str(n.get('id')):n for n in nodes}; re={str(e.get('id')):e for e in edges}
    new_nodes=[rn[k] for k in sorted(rn.keys()-dn.keys())]
    new_edges=[re[k] for k in sorted(re.keys()-de.keys())]
    reconnected=[]
    for k in sorted(re.keys() & de.keys()):
        if str(re[k].get('source') or '')!=str(de[k].get('source') or '') or str(re[k].get('target') or '')!=str(de[k].get('target') or ''):
            reconnected.append({'stream_id':k,'design':{'source':de[k].get('source'),'target':de[k].get('target')},'retrofit':{'source':re[k].get('source'),'target':re[k].get('target')}})
    secondary=[n for n in nodes if str(n.get('id')) not in set(main_ids) and n.get('type') not in PASSIVE_TYPES]
    return {
      'status':'analyzed' if main_ids else 'partial',
      'main_feed':{'stream_id':(feed or {}).get('stream_id'),'name':(feed or {}).get('name'),'target_equipment_id':start},
      'target_product':{'stream_id':(product or {}).get('stream_id'),'name':(product or {}).get('name'),'source_equipment_id':target},
      'main_path':{'node_ids':main_ids,'units':main_units,'process_units':main_process},
      'secondary_process_units':secondary,
      'detour_paths':detours,
      'recycle_connections':recycle,
      'retrofit_changes':{'new_nodes':new_nodes,'new_edges':new_edges,'reconnected_edges':reconnected},
      'interpretation_rule':'main_path is the shortest feed-to-target-product path; detour/recycle paths are reported separately and are never flattened into one false linear sequence.'
    }
