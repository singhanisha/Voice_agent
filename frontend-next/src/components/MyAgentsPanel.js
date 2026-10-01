'use client';

import { useCallback, useEffect, useState } from 'react';
import QATestModal from '@/components/QATestModal';
import TalkLiveModal from '@/components/TalkLiveModal';
import AgentStudio from '@/components/AgentStudio';
import {
  AGENT_TYPES,
  SMALLEST_FEMALE_VOICES,
  SMALLEST_MALE_VOICES,
  LANGUAGE_OPTIONS,
  DEFAULT_SMALLEST_VOICE,
  agentTypeLabel,
} from '@/lib/agentOptions';

const voiceLabel = (v) =>
  [...SMALLEST_FEMALE_VOICES, ...SMALLEST_MALE_VOICES].find((x) => x.value === v)?.label.split(' - ')[0] || v;

/**
 * MyAgentsPanel - client-facing agent builder.
 *  - list of this client's agents (multiple allowed)
 *  - create from a plain-language prompt
 *  - Agent Studio: edit prompt / nodes / JSON / voice, and live test call
 * Every request carries X-Tenant-ID / X-User-Email so the backend's tenant
 * scope checks only expose this client's own agents.
 */
export default function MyAgentsPanel({ user }) {
  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [showCreate, setShowCreate] = useState(false);
  const [agentName, setAgentName] = useState('');
  const [agentType, setAgentType] = useState('real_estate_sales');
  const [prompt, setPrompt] = useState('');
  const [language, setLanguage] = useState('English');
  const [smallestVoice, setSmallestVoice] = useState(DEFAULT_SMALLEST_VOICE);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generatedFlow, setGeneratedFlow] = useState(null);
  const [saving, setSaving] = useState(false);

  const [studioAgent, setStudioAgent] = useState(null);
  const [qaAgent, setQaAgent] = useState(null);
  const [talkLiveAgent, setTalkLiveAgent] = useState(null);

  const buildTenantHeaders = useCallback((json = false) => {
    const headers = json ? { 'Content-Type': 'application/json' } : {};
    if (user?.clientId) headers['X-Tenant-ID'] = user.clientId;
    if (user?.email) headers['X-User-Email'] = user.email;
    return headers;
  }, [user?.clientId, user?.email]);

  const fetchMyAgents = useCallback(async () => {
    if (!user?.email) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/api/agents?user_email=${encodeURIComponent(user.email)}`, {
        headers: buildTenantHeaders(),
      });
      if (!res.ok) throw new Error('Could not load your agents');
      const data = await res.json();
      setAgents(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || 'Could not load your agents');
    } finally {
      setLoading(false);
    }
  }, [API, user?.email, buildTenantHeaders]);

  useEffect(() => { fetchMyAgents(); }, [fetchMyAgents]);

  const resetCreateForm = () => {
    setAgentName('');
    setAgentType('real_estate_sales');
    setPrompt('');
    setLanguage('English');
    setSmallestVoice(DEFAULT_SMALLEST_VOICE);
    setGeneratedFlow(null);
    setGenerateError('');
  };

  const handleGenerateFlow = async () => {
    if (!prompt.trim()) return;
    setGenerating(true);
    setGenerateError('');
    try {
      const res = await fetch(`${API}/api/agents/generate-flow`, {
        method: 'POST',
        headers: buildTenantHeaders(true),
        body: JSON.stringify({ prompt, agent_name: agentName || null, agent_type: agentType, data_fields: null }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Generation failed');
      setGeneratedFlow(body.flow);
    } catch (err) {
      setGenerateError(err.message || 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const handleSaveAgent = async () => {
    if (!generatedFlow) return;
    setSaving(true);
    setGenerateError('');
    try {
      const res = await fetch(`${API}/api/agents`, {
        method: 'POST',
        headers: buildTenantHeaders(true),
        body: JSON.stringify({
          name: agentName || generatedFlow.agent_name || 'My Agent',
          agent_type: agentType,
          script: generatedFlow.conversationFlow?.global_prompt || prompt,
          data_fields: [],
          language,
          voice: smallestVoice,
          max_duration: 300,
          provider: 'twilio',
          stt_provider: 'smallest',
          tts_provider: 'smallest',
          assigned_email: user?.email || '',
          custom_json: generatedFlow,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not save agent');
      setShowCreate(false);
      resetCreateForm();
      await fetchMyAgents();
    } catch (err) {
      setGenerateError(err.message || 'Could not save agent');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
        <div>
          <h5 className="fw-bold mb-1">My Agents</h5>
          <p className="text-muted small mb-0">
            Describe an agent in plain words, then fine-tune its prompt, nodes and voice in the Studio.
          </p>
        </div>
        <button className="btn btn-dark rounded-pill px-4 fw-semibold" onClick={() => { resetCreateForm(); setShowCreate(true); }}>
          + Create Agent
        </button>
      </div>

      {error && <div className="alert alert-danger py-2 small">{error}</div>}

      {loading ? (
        <div className="text-center py-5"><span className="spinner-border" /></div>
      ) : agents.length === 0 ? (
        <div className="card border rounded-4">
          <div className="card-body text-center py-5">
            <p className="text-muted mb-3">You haven&apos;t created any agents yet.</p>
            <button className="btn btn-dark rounded-pill px-4" onClick={() => setShowCreate(true)}>+ Create your first agent</button>
          </div>
        </div>
      ) : (
        <div className="row g-3">
          {agents.map((agent) => (
            <div className="col-md-6 col-xl-4" key={agent.id}>
              <div className="card border rounded-4 h-100" style={{ boxShadow: 'none' }}>
                <div className="card-body d-flex flex-column p-4">
                  <div className="d-flex align-items-center gap-3 mb-3">
                    <div
                      className="d-flex align-items-center justify-content-center text-white fw-bold flex-shrink-0"
                      style={{ width: 44, height: 44, borderRadius: '50%', background: '#0A0A0A' }}
                    >
                      {(agent.name || 'A').slice(0, 1).toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <h6 className="fw-bold mb-0 text-truncate">{agent.name}</h6>
                      <div className="text-muted small">{agentTypeLabel(agent.agent_type)}</div>
                    </div>
                  </div>

                  <div className="d-flex flex-wrap gap-2 mb-3">
                    <span className="badge rounded-pill bg-light text-dark border">{agent.language || 'English'}</span>
                    <span className="badge rounded-pill bg-light text-dark border">Voice: {voiceLabel(agent.voice)}</span>
                    {agent.qa_score !== undefined && agent.qa_score !== null && (
                      <span className={`badge rounded-pill ${agent.qa_score >= 95 ? 'bg-success' : 'bg-secondary'}`}>
                        QA {agent.qa_score}/100
                      </span>
                    )}
                  </div>

                  <div className="mt-auto">
                    <button className="btn btn-dark rounded-pill w-100 fw-semibold mb-2" onClick={() => setStudioAgent(agent)}>
                      Open Studio (Edit nodes)
                    </button>
                    <div className="d-flex gap-2">
                      <button className="btn btn-outline-dark rounded-pill flex-fill btn-sm" onClick={() => setTalkLiveAgent(agent)}>
                        Test Run
                      </button>
                      <button className="btn btn-outline-secondary rounded-pill flex-fill btn-sm" onClick={() => setQaAgent(agent)}>
                        QA Report
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* CREATE AGENT */}
      {showCreate && (
        <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
          <div className="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
            <div className="modal-content border-0 shadow" style={{ borderRadius: 20 }}>
              <div className="modal-header border-0 pb-0 px-4 pt-4">
                <div>
                  <h5 className="modal-title fw-bold">Create Agent</h5>
                  <div className="text-muted small">Step {generatedFlow ? 2 : 1} of 2 - {generatedFlow ? 'Review & save' : 'Describe your agent'}</div>
                </div>
                <button type="button" className="btn-close" onClick={() => setShowCreate(false)} />
              </div>

              <div className="modal-body px-4">
                {generateError && <div className="alert alert-danger py-2 small">{generateError}</div>}

                {!generatedFlow ? (
                  <>
                    <div className="row g-3 mb-3">
                      <div className="col-md-6">
                        <label className="form-label small fw-semibold">Agent name</label>
                        <input className="form-control" value={agentName} onChange={(e) => setAgentName(e.target.value)} placeholder="e.g. Aisha" />
                      </div>
                      <div className="col-md-6">
                        <label className="form-label small fw-semibold">Agent type</label>
                        <select className="form-select" value={agentType} onChange={(e) => setAgentType(e.target.value)}>
                          {AGENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                        </select>
                      </div>
                      <div className="col-md-6">
                        <label className="form-label small fw-semibold">Language</label>
                        <select className="form-select" value={language} onChange={(e) => setLanguage(e.target.value)}>
                          {LANGUAGE_OPTIONS.map((l) => <option key={l} value={l}>{l}</option>)}
                        </select>
                      </div>
                      <div className="col-md-6">
                        <label className="form-label small fw-semibold">Voice (Smallest AI)</label>
                        <select className="form-select" value={smallestVoice} onChange={(e) => setSmallestVoice(e.target.value)}>
                          <optgroup label="Female voices">
                            {SMALLEST_FEMALE_VOICES.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                          </optgroup>
                          <optgroup label="Male voices">
                            {SMALLEST_MALE_VOICES.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                          </optgroup>
                        </select>
                      </div>
                    </div>
                    <label className="form-label small fw-semibold">What should this agent do?</label>
                    <textarea
                      className="form-control"
                      rows={6}
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      placeholder="e.g. A life-insurance advisor who calls leads, collects age, city, dependents and budget, then confirms details."
                    />
                  </>
                ) : (
                  <>
                    <div className="alert alert-success py-2 small">
                      Flow generated with {generatedFlow.conversationFlow?.nodes?.length || 0} nodes. Save now - you can edit every node in the Studio afterwards.
                    </div>
                    <div className="d-flex flex-column gap-2" style={{ maxHeight: 360, overflowY: 'auto' }}>
                      {(generatedFlow.conversationFlow?.nodes || []).map((node, i) => (
                        <div key={node.id} className="border rounded-3 p-3">
                          <div className="d-flex justify-content-between align-items-center mb-1">
                            <span className="fw-semibold small">{i + 1}. {node.name || node.id}</span>
                            <span className="badge bg-light text-dark border text-capitalize">{node.type}</span>
                          </div>
                          <div className="small text-muted">{node.response || node.instruction?.text}</div>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>

              <div className="modal-footer border-0 px-4 pb-4">
                <button className="btn btn-light border rounded-pill px-4" onClick={() => setShowCreate(false)}>Cancel</button>
                {!generatedFlow ? (
                  <button className="btn btn-dark rounded-pill px-4" disabled={generating || !prompt.trim()} onClick={handleGenerateFlow}>
                    {generating ? 'Generating...' : 'Generate Flow'}
                  </button>
                ) : (
                  <>
                    <button className="btn btn-outline-secondary rounded-pill px-4" onClick={() => setGeneratedFlow(null)}>Back</button>
                    <button className="btn btn-dark rounded-pill px-4" disabled={saving} onClick={handleSaveAgent}>
                      {saving ? 'Saving...' : 'Save Agent'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {studioAgent && (
        <AgentStudio
          agent={studioAgent}
          user={user}
          API={API}
          buildHeaders={buildTenantHeaders}
          onClose={() => setStudioAgent(null)}
          onSaved={fetchMyAgents}
        />
      )}

      {talkLiveAgent && (
        <TalkLiveModal agent={talkLiveAgent} userName={user?.name || user?.email} onClose={() => setTalkLiveAgent(null)} />
      )}

      {qaAgent && (
        <QATestModal
          agent={qaAgent}
          onClose={() => setQaAgent(null)}
          API={API}
          headers={buildTenantHeaders(true)}
          onCertifySuccess={fetchMyAgents}
        />
      )}
    </div>
  );
}