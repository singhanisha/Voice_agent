'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Save, ChevronDown, ChevronRight, Plus, Trash2, Mic, Bot, User, X } from 'lucide-react';
import { useVoiceSocket } from '@/hooks/useVoiceSocket';
import QATestModal from '@/components/QATestModal';
import {
  AGENT_TYPES,
  SMALLEST_FEMALE_VOICES,
  SMALLEST_MALE_VOICES,
  LANGUAGE_OPTIONS,
  DEFAULT_SMALLEST_VOICE,
  agentTypeLabel,
} from '@/lib/agentOptions';

const clone = (v) => JSON.parse(JSON.stringify(v));
const NODE_TYPES = ['conversation', 'fallback', 'end'];

const collectsToText = (c) => (Array.isArray(c) ? c.join(', ') : c || '');
const textToCollects = (t) => {
  const parts = String(t || '').split(',').map((x) => x.trim()).filter(Boolean);
  if (parts.length === 0) return undefined;
  return parts.length === 1 ? parts[0] : parts;
};
const edgeCondition = (e) => e?.transition_condition?.prompt || e?.condition || '';

/* ───────────────────────── Live test panel ───────────────────────── */
function TestPanel({ agent, userName, dirty, onOpenQA }) {
  const { connect, disconnect, isConnected, statusText, transcripts, clearTranscripts } =
    useVoiceSocket(agent?.id, agent?.client_id);

  useEffect(() => () => disconnect(), [disconnect]);

  return (
    <div className="as-test-inner">
      <div className="as-panel-head">
        <span>Test Audio</span>
        <button className="as-link" onClick={onOpenQA}>QA Report</button>
      </div>

      <div className="as-call-card">
        <div className={`as-mic ${isConnected ? 'live' : ''}`}><Mic size={26} /></div>
        <div className="as-status">{statusText || 'Ready'}</div>
        <button
          className={`as-btn ${isConnected ? 'danger' : 'dark'} w-100`}
          onClick={() => (isConnected ? disconnect() : connect(false, userName || 'Test User'))}
        >
          {isConnected ? 'End Call' : 'Start Test Call'}
        </button>
        {dirty && !isConnected && (
          <div className="as-warn">You have unsaved changes. The call runs on the last saved version - save first.</div>
        )}
      </div>

      <div className="as-panel-head" style={{ marginTop: 18 }}>
        <span>Transcript</span>
        <button className="as-link" onClick={clearTranscripts}>Clear</button>
      </div>
      <div className="as-transcript">
        {transcripts.length === 0 && <div className="as-empty">Conversation will appear here</div>}
        {transcripts.map((m, i) => {
          const isAgent = m.speaker === 'agent';
          return (
            <div key={i} className={`as-msg ${isAgent ? 'agent' : 'user'}`}>
              <div className="as-msg-who">{isAgent ? <><Bot size={11} /> Agent</> : <><User size={11} /> You</>}</div>
              <div>{m.text}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ───────────────────────── Studio ───────────────────────── */
export default function AgentStudio({ agent, user, API, buildHeaders, onClose, onSaved }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(null);
  const [saving, setSaving] = useState(false);

  const [schema, setSchema] = useState(null);
  const [meta, setMeta] = useState({
    name: agent?.name || '',
    language: agent?.language || 'English',
    voice: agent?.voice || DEFAULT_SMALLEST_VOICE,
    agentType: agent?.agent_type || 'real_estate_sales',
    globalPrompt: '',
  });
  const [baseline, setBaseline] = useState('');

  const [tab, setTab] = useState('prompt');
  const [openNode, setOpenNode] = useState(null);
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState('');
  const [sections, setSections] = useState({ voice: true, fields: true, info: false });
  const [showQA, setShowQA] = useState(false);
  const [insertForm, setInsertForm] = useState(null); // {afterId, edgeIdx, name, response, collects}

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/api/agents/${agent.id}/runtime-schema`, { headers: buildHeaders() });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not load this agent');
      const s = body.schema || {};
      const a = body.agent || agent;
      const nextMeta = {
        name: a.name || agent.name || '',
        language: a.language || 'English',
        voice: a.voice || DEFAULT_SMALLEST_VOICE,
        agentType: a.agent_type || 'real_estate_sales',
        globalPrompt: s.global_prompt || s.conversationFlow?.global_prompt || a.script || '',
      };
      setSchema(s);
      setMeta(nextMeta);
      setBaseline(JSON.stringify({ meta: nextMeta, schema: s }));
    } catch (err) {
      setError(err.message || 'Could not load this agent');
    } finally {
      setLoading(false);
    }
  }, [API, agent, buildHeaders]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.id]);

  const dirty = useMemo(
    () => !!schema && JSON.stringify({ meta, schema }) !== baseline,
    [meta, schema, baseline],
  );

  const flow = schema?.conversationFlow || {};
  const nodes = Array.isArray(flow.nodes) ? flow.nodes : [];
  const nodeIds = nodes.map((n) => n.id);
  const startId = flow.start_node_id;

  const mutate = (fn) => setSchema((prev) => { const next = clone(prev); fn(next); return next; });
  const mutateNode = (idx, fn) => mutate((s) => fn(s.conversationFlow.nodes[idx]));

  const setMetaField = (k, v) => setMeta((m) => ({ ...m, [k]: v }));

  const reachable = (() => {
    const seen = new Set();
    const stack = startId ? [startId] : [];
    while (stack.length) {
      const id = stack.pop();
      if (seen.has(id)) continue;
      seen.add(id);
      const n = nodes.find((x) => x.id === id);
      (n?.edges || []).forEach((e) => stack.push(e.destination_node_id));
    }
    return seen;
  })();
  const startNode = nodes.find((n) => n.id === startId);
  const setWelcome = (text) => {
    const idx = nodes.findIndex((n) => n.id === startId);
    if (idx >= 0) mutateNode(idx, (n) => { n.response = text; });
  };

  // Open the "insert node" form. Default position: right before the ending
  // (goodbye) step, since that is where people usually want to add a question.
  const openInsert = (afterId, edgeIdx = 0) => {
    let target = afterId;
    if (!target) {
      const endIds = nodes.filter((n) => n.type === 'end').map((n) => n.id);
      const before = nodes.find((n) => (n.edges || []).some((e) => endIds.includes(e.destination_node_id)));
      target = (before || nodes[0])?.id;
    }
    setInsertForm({ afterId: target, edgeIdx, name: '', response: '', collects: '' });
    setTab('nodes');
  };

  const submitInsert = () => {
    const f = insertForm;
    if (!f) return;
    const aIdx = nodes.findIndex((n) => n.id === f.afterId);
    if (aIdx < 0) return;
    if (!f.name.trim() || !f.response.trim()) {
      setNotice({ type: 'err', text: 'Give the new node a name and what the agent should say.' });
      return;
    }
    const id = `node-custom-${Date.now() % 100000}`;
    const collects = textToCollects(f.collects);
    const collectsText = collectsToText(collects);
    const cond = collectsText ? `Caller provides their ${collectsText}.` : 'Caller responds.';
    mutate((sc) => {
      const list = sc.conversationFlow.nodes;
      const A = list[aIdx];
      A.edges = A.edges || [];
      const newNode = {
        id,
        name: f.name.trim(),
        type: 'conversation',
        instruction: { type: 'prompt', text: collectsText ? `Ask the caller for their ${collectsText}.` : f.response.trim() },
        response: f.response.trim(),
        edges: [],
      };
      if (collects !== undefined) newNode.collects = collects;
      const link = (dest) => ({
        id: `edge-${id}`,
        condition: cond,
        transition_condition: { type: 'prompt', prompt: cond },
        destination_node_id: dest,
      });
      if (A.edges.length > 0) {
        const edge = A.edges[Math.min(f.edgeIdx, A.edges.length - 1)];
        newNode.edges = [link(edge.destination_node_id)]; // new node continues to the old destination
        edge.destination_node_id = id;                    // and the old path now goes through the new node
      } else {
        A.edges.push({
          id: `edge-to-${id}`,
          condition: 'user responds',
          transition_condition: { type: 'prompt', prompt: 'user responds' },
          destination_node_id: id,
        });
      }
      list.splice(aIdx + 1, 0, newNode);
    });
    setInsertForm(null);
    setOpenNode(id);
    setNotice({ type: 'ok', text: `Node added after "${nodes[aIdx].name || nodes[aIdx].id}" and linked into the flow. Click Save to make it live.` });
  };

  const deleteNode = (idx) => {
    const id = nodes[idx].id;
    if (id === startId) return;
    if (!window.confirm(`Delete node "${nodes[idx].name || id}"? Links pointing to it will be removed.`)) return;
    mutate((s) => {
      s.conversationFlow.nodes.splice(idx, 1);
      s.conversationFlow.nodes.forEach((n) => {
        n.edges = (n.edges || []).filter((e) => e.destination_node_id !== id);
      });
    });
  };

  const addEdge = (idx) => mutateNode(idx, (n) => {
    n.edges = n.edges || [];
    n.edges.push({
      id: `edge-${Date.now() % 100000}`,
      condition: 'user responds',
      transition_condition: { type: 'prompt', prompt: 'user responds' },
      destination_node_id: nodeIds.find((x) => x !== n.id) || n.id,
    });
  });

  const setEdgeCondition = (ni, ei, text) => mutateNode(ni, (n) => {
    n.edges[ei].condition = text;
    n.edges[ei].transition_condition = { type: 'prompt', prompt: text };
  });

  const gatherFields = () => {
    const set = new Set();
    nodes.forEach((n) => {
      const c = n.collects;
      (Array.isArray(c) ? c : c ? [c] : []).forEach((x) => set.add(String(x)));
    });
    return [...set];
  };

  const switchTab = (t) => {
    if (t === 'json') { setJsonText(JSON.stringify(schema, null, 2)); setJsonError(''); }
    setTab(t);
  };

  const applyJson = () => {
    try {
      const parsed = JSON.parse(jsonText);
      const ns = parsed?.conversationFlow?.nodes;
      if (!Array.isArray(ns) || ns.length === 0) throw new Error('conversationFlow.nodes must be a non-empty list');
      if (!ns.some((n) => n.id === parsed.conversationFlow.start_node_id)) {
        throw new Error('conversationFlow.start_node_id must match one of the node ids');
      }
      setSchema(parsed);
      setMeta((m) => ({
        ...m,
        globalPrompt: parsed.global_prompt || parsed.conversationFlow?.global_prompt || m.globalPrompt,
      }));
      setJsonError('');
      setNotice({ type: 'ok', text: 'JSON applied. Click Save to make it live.' });
    } catch (err) {
      setJsonError(err.message);
    }
  };

  const validate = () => {
    if (!meta.name.trim()) return 'Agent name cannot be empty.';
    if (nodes.length === 0) return 'Agent needs at least one node.';
    for (const n of nodes) {
      for (const e of n.edges || []) {
        if (!nodeIds.includes(e.destination_node_id)) {
          return `Node "${n.name || n.id}" links to a missing node "${e.destination_node_id}".`;
        }
      }
    }
    return '';
  };

  const save = async () => {
    const problem = validate();
    if (problem) { setNotice({ type: 'err', text: problem }); return; }
    const orphans = nodes.filter((n) => !reachable.has(n.id));
    if (orphans.length > 0 && !window.confirm(
      `These nodes are not connected to the flow and will never be spoken:\n- ${orphans.map((n) => n.name || n.id).join('\n- ')}\n\nSave anyway?`,
    )) return;
    setSaving(true);
    setNotice(null);
    try {
      const custom = clone(schema);
      custom.global_prompt = meta.globalPrompt;
      if (custom.conversationFlow) custom.conversationFlow.global_prompt = meta.globalPrompt;
      const res = await fetch(`${API}/api/agents/${agent.id}`, {
        method: 'PUT',
        headers: buildHeaders(true),
        body: JSON.stringify({
          name: meta.name.trim(),
          language: meta.language,
          voice: meta.voice,
          agent_type: meta.agentType,
          tts_provider: 'smallest',
          stt_provider: 'smallest',
          script: meta.globalPrompt,
          custom_json: custom,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Save failed');
      await load();
      setNotice({ type: 'ok', text: 'Saved. Your next test call uses this version.' });
      onSaved?.();
    } catch (err) {
      setNotice({ type: 'err', text: err.message || 'Save failed' });
    } finally {
      setSaving(false);
    }
  };

  const tryClose = () => {
    if (dirty && !window.confirm('You have unsaved changes. Close without saving?')) return;
    onClose();
  };

  const toggle = (k) => setSections((s) => ({ ...s, [k]: !s[k] }));

  /* ───────── render ───────── */
  return (
    <div className="as-root">
      <style>{CSS}</style>

      <header className="as-top">
        <button className="as-icon-btn" onClick={tryClose} aria-label="Back"><ArrowLeft size={18} /></button>
        <div className="as-title">
          <input
            className="as-title-input"
            value={meta.name}
            onChange={(e) => setMetaField('name', e.target.value)}
            disabled={loading}
          />
          <span className="as-chip">{agentTypeLabel(meta.agentType)}</span>
          {dirty && <span className="as-chip warn">Unsaved</span>}
        </div>
        <div className="as-top-actions">
          <button className="as-btn dark" onClick={save} disabled={saving || loading || !dirty}>
            <Save size={14} /> {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </header>

      {notice && (
        <div className={`as-notice ${notice.type}`}>
          <span>{notice.text}</span>
          <button className="as-icon-btn" onClick={() => setNotice(null)}><X size={14} /></button>
        </div>
      )}

      {loading ? (
        <div className="as-center"><span className="spinner-border" /></div>
      ) : error ? (
        <div className="as-center">
          <div style={{ textAlign: 'center' }}>
            <p style={{ color: '#dc2626' }}>{error}</p>
            <button className="as-btn dark" onClick={load}>Retry</button>
          </div>
        </div>
      ) : (
        <div className="as-body">
          {/* ── MAIN EDITOR ── */}
          <section className="as-col as-main">
            <div className="as-tabs">
              {[['prompt', 'Prompt'], ['nodes', `Nodes (${nodes.length})`], ['json', 'JSON']].map(([k, label]) => (
                <button key={k} className={`as-tab ${tab === k ? 'active' : ''}`} onClick={() => switchTab(k)}>{label}</button>
              ))}
            </div>

            {tab === 'prompt' && (
              <div className="as-pad">
                <label className="as-label">Global prompt</label>
                <p className="as-hint">Personality, tone and rules the agent follows in every node.</p>
                <textarea
                  className="as-textarea"
                  style={{ minHeight: 280 }}
                  value={meta.globalPrompt}
                  onChange={(e) => setMetaField('globalPrompt', e.target.value)}
                />
                <label className="as-label" style={{ marginTop: 18 }}>Welcome message</label>
                <p className="as-hint">First thing the agent says when the call connects (start node: {startId || '-'}). Use {'{{name}}'} for the lead&apos;s name.</p>
                <textarea
                  className="as-textarea"
                  style={{ minHeight: 90 }}
                  value={startNode?.response || ''}
                  onChange={(e) => setWelcome(e.target.value)}
                />
              </div>
            )}

            {tab === 'nodes' && (
              <div className="as-pad">
                <div className="as-row-between">
                  <p className="as-hint" style={{ margin: 0 }}>Each node is one step of the call. Edit what the agent says and where the call goes next.</p>
                  <button className="as-btn light" onClick={() => openInsert()}><Plus size={14} /> Add node</button>
                </div>

                {insertForm && (() => {
                  const A = nodes.find((n) => n.id === insertForm.afterId);
                  const aEdges = A?.edges || [];
                  const nameOf = (id) => nodes.find((n) => n.id === id)?.name || id;
                  return (
                    <div className="as-insert">
                      <div className="as-row-between">
                        <strong>Add a new node</strong>
                        <button className="as-icon-btn" onClick={() => setInsertForm(null)} aria-label="Cancel"><X size={14} /></button>
                      </div>

                      <label className="as-label">Insert after</label>
                      <select
                        className="as-input"
                        value={insertForm.afterId}
                        onChange={(e) => setInsertForm((f) => ({ ...f, afterId: e.target.value, edgeIdx: 0 }))}
                      >
                        {nodes.map((n, i) => <option key={n.id} value={n.id}>{i + 1}. {n.name || n.id}</option>)}
                      </select>

                      {aEdges.length > 1 && (
                        <>
                          <label className="as-label">On which path?</label>
                          <select
                            className="as-input"
                            value={insertForm.edgeIdx}
                            onChange={(e) => setInsertForm((f) => ({ ...f, edgeIdx: Number(e.target.value) }))}
                          >
                            {aEdges.map((e, i) => (
                              <option key={e.id || i} value={i}>{edgeCondition(e) || 'next'} → {nameOf(e.destination_node_id)}</option>
                            ))}
                          </select>
                        </>
                      )}

                      <div className="as-hint" style={{ marginTop: 8 }}>
                        {aEdges.length === 0
                          ? 'This step is an ending, so the new node will be attached after it.'
                          : `Flow will become: ${A?.name || A?.id} → (new node) → ${nameOf(aEdges[Math.min(insertForm.edgeIdx, aEdges.length - 1)]?.destination_node_id)}`}
                      </div>

                      <div className="as-grid2">
                        <div>
                          <label className="as-label">Node name</label>
                          <input className="as-input" value={insertForm.name} onChange={(e) => setInsertForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Ask City" />
                        </div>
                        <div>
                          <label className="as-label">Collects (optional)</label>
                          <input className="as-input" value={insertForm.collects} onChange={(e) => setInsertForm((f) => ({ ...f, collects: e.target.value }))} placeholder="e.g. city" />
                        </div>
                      </div>
                      <label className="as-label">Agent says</label>
                      <textarea className="as-textarea" style={{ minHeight: 64 }} value={insertForm.response} onChange={(e) => setInsertForm((f) => ({ ...f, response: e.target.value }))} placeholder="e.g. Which city do you live in?" />

                      <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                        <button className="as-btn dark" onClick={submitInsert}>Insert node</button>
                        <button className="as-btn light" onClick={() => setInsertForm(null)}>Cancel</button>
                      </div>
                    </div>
                  );
                })()}
                <div className="as-nodes">
                  {nodes.map((n, idx) => {
                    const open = openNode === n.id;
                    return (
                      <div key={n.id} className={`as-node ${open ? 'open' : ''}`}>
                        <button className="as-node-head" onClick={() => setOpenNode(open ? null : n.id)}>
                          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                          <span className="as-node-idx">{idx + 1}</span>
                          <span className="as-node-name">{n.name || n.id}</span>
                          {n.id === startId && <span className="as-chip green">Start</span>}
                          {!reachable.has(n.id) && <span className="as-chip red" title="No path from the start reaches this node - the agent will never say it">Not connected</span>}
                          <span className="as-chip">{n.type || 'conversation'}</span>
                          {n.collects && <span className="as-chip blue">collects: {collectsToText(n.collects)}</span>}
                        </button>

                        {open && (
                          <div className="as-node-body">
                            <div className="as-grid2">
                              <div>
                                <label className="as-label">Name</label>
                                <input className="as-input" value={n.name || ''} onChange={(e) => mutateNode(idx, (x) => { x.name = e.target.value; })} />
                              </div>
                              <div>
                                <label className="as-label">Type</label>
                                <select className="as-input" value={n.type || 'conversation'} onChange={(e) => mutateNode(idx, (x) => { x.type = e.target.value; })}>
                                  {[...new Set([...NODE_TYPES, n.type].filter(Boolean))].map((t) => <option key={t} value={t}>{t}</option>)}
                                </select>
                              </div>
                            </div>

                            <label className="as-label">Agent says</label>
                            <textarea className="as-textarea" style={{ minHeight: 80 }} value={n.response || ''} onChange={(e) => mutateNode(idx, (x) => { x.response = e.target.value; })} />

                            <label className="as-label">Instruction (what this step should do)</label>
                            <textarea className="as-textarea" style={{ minHeight: 64 }} value={n.instruction?.text || ''} onChange={(e) => mutateNode(idx, (x) => { x.instruction = { type: 'prompt', ...(x.instruction || {}), text: e.target.value }; })} />

                            <label className="as-label">Collects (comma separated field names)</label>
                            <input
                              className="as-input"
                              value={collectsToText(n.collects)}
                              onChange={(e) => mutateNode(idx, (x) => {
                                const v = textToCollects(e.target.value);
                                if (v === undefined) delete x.collects; else x.collects = v;
                              })}
                              placeholder="e.g. name, budget"
                            />

                            <div className="as-row-between" style={{ marginTop: 14 }}>
                              <label className="as-label" style={{ margin: 0 }}>Next steps</label>
                              <button className="as-link" onClick={() => addEdge(idx)}>+ Add link</button>
                            </div>
                            {(n.edges || []).length === 0 && <div className="as-empty" style={{ padding: 8 }}>No next step (call ends here).</div>}
                            {(n.edges || []).map((e, ei) => (
                              <div key={e.id || ei} className="as-edge">
                                <input className="as-input" value={edgeCondition(e)} onChange={(ev) => setEdgeCondition(idx, ei, ev.target.value)} placeholder="When..." />
                                <span className="as-arrow">→</span>
                                <select className="as-input" value={e.destination_node_id} onChange={(ev) => mutateNode(idx, (x) => { x.edges[ei].destination_node_id = ev.target.value; })}>
                                  {!nodeIds.includes(e.destination_node_id) && <option value={e.destination_node_id}>{e.destination_node_id} (missing)</option>}
                                  {nodes.map((o) => <option key={o.id} value={o.id}>{o.name || o.id}</option>)}
                                </select>
                                <span style={{ display: 'inline-flex' }}>
                                  <button className="as-icon-btn" title="Insert a new node on this path" onClick={() => openInsert(n.id, ei)} aria-label="Insert node here"><Plus size={14} /></button>
                                  <button className="as-icon-btn" title="Remove link" onClick={() => mutateNode(idx, (x) => { x.edges.splice(ei, 1); })} aria-label="Remove link"><Trash2 size={14} /></button>
                                </span>
                              </div>
                            ))}

                            <div style={{ marginTop: 14, textAlign: 'right' }}>
                              <button className="as-link danger" disabled={n.id === startId} onClick={() => deleteNode(idx)}>
                                {n.id === startId ? 'Start node cannot be deleted' : 'Delete node'}
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {tab === 'json' && (
              <div className="as-pad">
                <p className="as-hint">Full agent definition. Advanced: edit carefully, then Apply and Save.</p>
                <textarea
                  className="as-textarea mono"
                  style={{ minHeight: 480 }}
                  spellCheck={false}
                  value={jsonText}
                  onChange={(e) => setJsonText(e.target.value)}
                />
                {jsonError && <div className="as-notice err" style={{ margin: '10px 0 0' }}>{jsonError}</div>}
                <div style={{ marginTop: 10 }}>
                  <button className="as-btn light" onClick={applyJson}>Apply JSON</button>
                </div>
              </div>
            )}
          </section>

          {/* ── SETTINGS ── */}
          <aside className="as-col as-settings">
            <div className="as-acc">
              <button className="as-acc-head" onClick={() => toggle('voice')}>
                <span>Voice &amp; Language</span>{sections.voice ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </button>
              {sections.voice && (
                <div className="as-acc-body">
                  <label className="as-label">Agent type</label>
                  <select className="as-input" value={meta.agentType} onChange={(e) => setMetaField('agentType', e.target.value)}>
                    {AGENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                  <label className="as-label">Language</label>
                  <select className="as-input" value={meta.language} onChange={(e) => setMetaField('language', e.target.value)}>
                    {[...new Set([...LANGUAGE_OPTIONS, meta.language])].map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                  <label className="as-label">Voice (Smallest AI)</label>
                  <select className="as-input" value={meta.voice} onChange={(e) => setMetaField('voice', e.target.value)}>
                    <optgroup label="Female">
                      {SMALLEST_FEMALE_VOICES.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                    </optgroup>
                    <optgroup label="Male">
                      {SMALLEST_MALE_VOICES.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                    </optgroup>
                  </select>
                  <div className="as-hint" style={{ marginTop: 8 }}>Speech-to-text and text-to-speech both run on Smallest AI.</div>
                </div>
              )}
            </div>

            <div className="as-acc">
              <button className="as-acc-head" onClick={() => toggle('fields')}>
                <span>Data collected</span>{sections.fields ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </button>
              {sections.fields && (
                <div className="as-acc-body">
                  {gatherFields().length === 0
                    ? <div className="as-hint">No fields collected yet.</div>
                    : <div className="as-chips">{gatherFields().map((f) => <span key={f} className="as-chip blue">{f}</span>)}</div>}
                </div>
              )}
            </div>

            <div className="as-acc">
              <button className="as-acc-head" onClick={() => toggle('info')}>
                <span>Agent info</span>{sections.info ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </button>
              {sections.info && (
                <div className="as-acc-body as-hint">
                  <div>ID: <span className="mono">{agent.id}</span></div>
                  <div>Nodes: {nodes.length}</div>
                  <div>Start node: <span className="mono">{startId}</span></div>
                </div>
              )}
            </div>
          </aside>

          {/* ── TEST ── */}
          <aside className="as-col as-test">
            <TestPanel
              agent={agent}
              userName={user?.name || user?.email}
              dirty={dirty}
              onOpenQA={() => setShowQA(true)}
            />
          </aside>
        </div>
      )}

      {showQA && (
        <QATestModal
          agent={agent}
          onClose={() => setShowQA(false)}
          API={API}
          headers={buildHeaders(true)}
          onCertifySuccess={onSaved}
        />
      )}
    </div>
  );
}

/* ───────────────────────── styles ───────────────────────── */
const CSS = `
.as-root{position:fixed;inset:0;z-index:1050;background:#F7F7F5;display:flex;flex-direction:column;color:#0A0A0A;font-size:14px}
.as-top{display:flex;align-items:center;gap:12px;padding:12px 20px;background:#fff;border-bottom:1px solid #E5E5E5}
.as-title{display:flex;align-items:center;gap:10px;flex:1;min-width:0}
.as-title-input{border:1px solid transparent;background:transparent;font-size:18px;font-weight:600;padding:4px 8px;border-radius:8px;min-width:120px;max-width:340px}
.as-title-input:hover,.as-title-input:focus{border-color:#E5E5E5;outline:none;background:#fff}
.as-top-actions{display:flex;gap:8px}
.as-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid #0A0A0A;border-radius:999px;padding:9px 20px;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;cursor:pointer;transition:opacity .15s}
.as-btn.dark{background:#0A0A0A;color:#fff}.as-btn.light{background:#fff;color:#0A0A0A;border-color:#D4D4D4}.as-btn.danger{background:#dc2626;border-color:#dc2626;color:#fff}
.as-btn:disabled{opacity:.4;cursor:not-allowed}
.w-100{width:100%}
.as-icon-btn{border:none;background:transparent;padding:6px;border-radius:8px;cursor:pointer;color:#404040;display:inline-flex}
.as-icon-btn:hover{background:#F0F0F0}
.as-link{border:none;background:none;color:#2563eb;font-size:12px;cursor:pointer;padding:0}
.as-link.danger{color:#dc2626}.as-link:disabled{color:#A3A3A3;cursor:default}
.as-chip{display:inline-block;font-size:11px;padding:2px 9px;border-radius:999px;background:#F0F0F0;color:#404040;white-space:nowrap}
.as-chip.red{background:#FEE2E2;color:#991B1B}.as-insert{margin-top:12px;padding:14px 16px;border:1px dashed #0A0A0A;border-radius:14px;background:#FCFCFC}
.as-edge{grid-template-columns:1fr auto 1fr auto}
.as-chip.warn{background:#FEF3C7;color:#92400E}.as-chip.green{background:#DCFCE7;color:#166534}.as-chip.blue{background:#E0EAFF;color:#1E40AF}
.as-notice{display:flex;justify-content:space-between;align-items:center;margin:10px 20px 0;padding:8px 14px;border-radius:10px;font-size:13px}
.as-notice.ok{background:#DCFCE7;color:#166534}.as-notice.err{background:#FEE2E2;color:#991B1B}
.as-center{flex:1;display:flex;align-items:center;justify-content:center}
.as-body{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,.9fr) minmax(0,1fr);gap:14px;padding:14px 20px 20px}
.as-col{background:#fff;border:1px solid #E5E5E5;border-radius:18px;overflow-y:auto;min-height:0}
.as-settings{background:transparent;border:none;display:flex;flex-direction:column;gap:12px;overflow-y:auto}
.as-tabs{display:flex;gap:4px;padding:10px 14px 0;border-bottom:1px solid #EEE;position:sticky;top:0;background:#fff;z-index:2}
.as-tab{border:none;background:none;padding:10px 14px;font-weight:500;color:#737373;border-bottom:2px solid transparent;cursor:pointer}
.as-tab.active{color:#0A0A0A;border-bottom-color:#0A0A0A;font-weight:600}
.as-pad{padding:18px 20px 28px}
.as-label{display:block;font-size:12px;font-weight:600;color:#262626;margin:12px 0 5px}
.as-hint{font-size:12px;color:#737373;margin:0 0 8px}
.as-input,.as-textarea{width:100%;border:1px solid #E0E0E0;border-radius:10px;padding:8px 11px;font-size:13px;background:#FCFCFC;color:#0A0A0A}
.as-input:focus,.as-textarea:focus{outline:none;border-color:#0A0A0A;background:#fff}
.as-textarea{resize:vertical;line-height:1.5}
.as-textarea.mono,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.as-row-between{display:flex;justify-content:space-between;align-items:center;gap:12px}
.as-grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.as-nodes{margin-top:14px;display:flex;flex-direction:column;gap:8px}
.as-node{border:1px solid #E5E5E5;border-radius:14px;background:#fff;overflow:hidden}
.as-node.open{border-color:#0A0A0A}
.as-node-head{width:100%;display:flex;align-items:center;gap:8px;padding:10px 12px;border:none;background:none;text-align:left;cursor:pointer;flex-wrap:wrap}
.as-node-idx{width:22px;height:22px;border-radius:50%;background:#0A0A0A;color:#fff;font-size:11px;display:inline-flex;align-items:center;justify-content:center}
.as-node-name{font-weight:600;flex:1;min-width:80px}
.as-node-body{padding:4px 16px 16px;border-top:1px solid #EEE;background:#FCFCFC}
.as-edge{display:grid;grid-template-columns:1fr auto 1fr auto;gap:8px;align-items:center;margin-top:8px}
.as-arrow{color:#737373}
.as-acc{background:#fff;border:1px solid #E5E5E5;border-radius:16px;overflow:hidden}
.as-acc-head{width:100%;display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border:none;background:none;font-weight:600;cursor:pointer}
.as-acc-body{padding:0 16px 16px}
.as-chips{display:flex;flex-wrap:wrap;gap:6px}
.as-test-inner{padding:16px}
.as-panel-head{display:flex;justify-content:space-between;align-items:center;font-weight:600;margin-bottom:10px}
.as-call-card{border:1px solid #EEE;border-radius:16px;padding:18px;text-align:center;background:#FCFCFC}
.as-mic{width:64px;height:64px;border-radius:50%;background:#0A0A0A;color:#fff;display:flex;align-items:center;justify-content:center;margin:0 auto 10px;transition:box-shadow .3s}
.as-mic.live{box-shadow:0 0 0 5px #10b981}
.as-status{font-size:12px;color:#737373;margin-bottom:12px;min-height:16px}
.as-warn{margin-top:10px;font-size:12px;color:#92400E;background:#FEF3C7;border-radius:8px;padding:6px 10px}
.as-transcript{display:flex;flex-direction:column;gap:8px;max-height:360px;overflow-y:auto}
.as-empty{text-align:center;color:#A3A3A3;font-size:12px;padding:24px 0}
.as-msg{border-radius:12px;padding:8px 12px;font-size:13px;max-width:92%}
.as-msg.agent{background:#F0F0F0;align-self:flex-start}.as-msg.user{background:#E0EAFF;align-self:flex-end}
.as-msg-who{display:flex;align-items:center;gap:4px;font-size:10px;text-transform:uppercase;font-weight:700;color:#737373;margin-bottom:2px}
@media (max-width:1100px){.as-body{grid-template-columns:1fr;overflow-y:auto}.as-col{overflow:visible}.as-settings{overflow:visible}}
`;