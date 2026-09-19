'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import FlowPreviewModal from '@/components/FlowPreviewModal';
import { useAuth } from '@/context/AuthContext';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const FLOW_VISUALIZATION_ENABLED = process.env.NEXT_PUBLIC_FLOW_VISUALIZATION_ENABLED === 'true';
const SCRAPE_GENERATE_SCRIPT_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_GENERATE_SCRIPT_ENABLED === 'true';
const SCRAPE_WORKER_V1_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_WORKER_V1_ENABLED === 'true';
const SCRAPE_JOB_CANCEL_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_JOB_CANCEL_ENABLED === 'true';
const SCRAPE_STALE_RECOVERY_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_STALE_RECOVERY_ENABLED === 'true';
const SCRAPE_JOB_EVENTS_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_JOB_EVENTS_ENABLED === 'true';
const SCRAPE_LIVE_QA_READINESS_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_LIVE_QA_READINESS_ENABLED === 'true';
const SCRAPE_GENERATED_DRAFT_QA_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_GENERATED_DRAFT_QA_ENABLED === 'true';

function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatEventType(value) {
  return String(value || 'event').replaceAll('_', ' ');
}

function statusClass(status) {
  if (status === 'failed') return 'bg-danger-subtle text-danger border-danger-subtle';
  if (status === 'completed' || status === 'draft_ready' || status === 'flow_draft_saved') return 'bg-success-subtle text-success border-success-subtle';
  if (status === 'running' || status === 'dispatching') return 'bg-primary-subtle text-primary border-primary-subtle';
  if (status === 'cancelled') return 'bg-warning-subtle text-warning border-warning-subtle';
  return 'bg-secondary-subtle text-secondary border-secondary-subtle';
}

function qualityClass(level) {
  if (level === 'high') return 'bg-success-subtle text-success border-success-subtle';
  if (level === 'medium') return 'bg-primary-subtle text-primary border-primary-subtle';
  if (level === 'low') return 'bg-warning-subtle text-warning border-warning-subtle';
  return 'bg-secondary-subtle text-secondary border-secondary-subtle';
}

function extractionQuality(job) {
  return job?.latest_extraction?.extraction?.quality || null;
}

function sourceEvidenceFromKnowledge(knowledge) {
  const values = [];
  const add = (items) => {
    (items || []).forEach((item) => {
      if (typeof item === 'string') values.push(item);
    });
  };
  if (knowledge?.source_url) values.push(knowledge.source_url);
  add(knowledge?.company?.evidence);
  (knowledge?.products_or_services || []).forEach((item) => add(item.evidence));
  (knowledge?.value_propositions || []).forEach((item) => add(item.evidence));
  (knowledge?.faqs || []).forEach((item) => add(item.evidence));
  (knowledge?.pages_crawled || []).forEach((page) => {
    if (page?.url) values.push(page.url);
  });
  return [...new Set(values.filter(Boolean))].slice(0, 8);
}

function contentInventoryItems(knowledge) {
  const pageTypes = knowledge?.content_inventory?.page_types || {};
  return Object.entries(pageTypes)
    .filter(([, count]) => Number(count) > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, 8);
}

function guidanceSummary(knowledge) {
  const questions = (knowledge?.qualification_questions || []).filter(Boolean).slice(0, 4);
  const objections = (knowledge?.objections || [])
    .filter((item) => item?.intent || item?.guidance)
    .slice(0, 4);
  const faqs = (knowledge?.faqs || [])
    .filter((item) => item?.question)
    .slice(0, 4);
  return {
    questions,
    objections,
    faqs,
    hasItems: Boolean(questions.length || objections.length || faqs.length),
  };
}

function ConversationGuidance({ knowledge }) {
  const guidance = guidanceSummary(knowledge);
  if (!guidance.hasItems) return null;

  return (
    <div className="border-top mt-3 pt-2">
      <div className="text-muted mb-1">Conversation Guidance</div>
      <div className="row g-2">
        {guidance.questions.length > 0 && (
          <div className="col-md-4">
            <div className="fw-semibold mb-1">Qualification</div>
            <ul className="mb-0 ps-3">
              {guidance.questions.map((question, index) => (
                <li key={`${question}-${index}`}>{question}</li>
              ))}
            </ul>
          </div>
        )}
        {guidance.objections.length > 0 && (
          <div className="col-md-4">
            <div className="fw-semibold mb-1">Objections</div>
            <ul className="mb-0 ps-3">
              {guidance.objections.map((item, index) => (
                <li key={`${item.intent || 'objection'}-${index}`}>
                  <span className="text-capitalize">{String(item.intent || 'objection').replaceAll('_', ' ')}</span>
                  {item.guidance ? `: ${item.guidance}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
        {guidance.faqs.length > 0 && (
          <div className="col-md-4">
            <div className="fw-semibold mb-1">FAQs</div>
            <ul className="mb-0 ps-3">
              {guidance.faqs.map((item, index) => (
                <li key={`${item.question}-${index}`}>{item.question}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function canDispatchJob(job) {
  return SCRAPE_WORKER_V1_ENABLED && ['queued', 'failed'].includes(job?.status || 'queued');
}

function canCancelJob(job) {
  return SCRAPE_JOB_CANCEL_ENABLED && ['queued', 'dispatching', 'running'].includes(job?.status || 'queued');
}

function canRecoverStaleJob(job) {
  return SCRAPE_STALE_RECOVERY_ENABLED
    && job?.health?.is_stale
    && ['dispatching', 'running'].includes(job?.status || 'queued');
}

function hasGeneratedDraft(job) {
  return Boolean((job?.drafts || []).length || (job?.diagnostics?.draft_count || 0) > 0);
}

function canCreateDraft(job) {
  if (!SCRAPE_GENERATE_SCRIPT_ENABLED || !job?.agent_id) return false;
  if (hasGeneratedDraft(job)) return false;
  if (!SCRAPE_WORKER_V1_ENABLED) return true;
  return Boolean(job?.latest_extraction?.extraction);
}

function canSaveDraftToFlow(draft) {
  return FLOW_VISUALIZATION_ENABLED && SCRAPE_GENERATE_SCRIPT_ENABLED && Boolean(draft?.id);
}

export default function IntelligencePage() {
  const { activeClient, user } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [jobActionKey, setJobActionKey] = useState('');
  const [selectedJob, setSelectedJob] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [readiness, setReadiness] = useState(null);
  const [readinessLoading, setReadinessLoading] = useState(false);
  const [liveQa, setLiveQa] = useState(null);
  const [liveQaLoading, setLiveQaLoading] = useState(false);
  const [generatedDraftQa, setGeneratedDraftQa] = useState(null);
  const [generatedDraftQaLoading, setGeneratedDraftQaLoading] = useState(false);
  const [flowPreviewAgent, setFlowPreviewAgent] = useState(null);
  const [flowPreview, setFlowPreview] = useState(null);
  const [flowPreviewLoading, setFlowPreviewLoading] = useState(false);
  const [flowPreviewError, setFlowPreviewError] = useState('');
  const [flowPreviewReadOnly, setFlowPreviewReadOnly] = useState(false);

  const clientId = user?.role === 'admin' ? activeClient : user?.clientId;

  const queryString = useMemo(() => {
    const params = new URLSearchParams({ limit: '50' });
    if (clientId) params.set('clientId', clientId);
    if (statusFilter) params.set('status', statusFilter);
    return params.toString();
  }, [clientId, statusFilter]);

  const loadJobs = useCallback(async () => {
    if (!SCRAPE_GENERATE_SCRIPT_ENABLED || user?.role !== 'admin' || !clientId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const res = await fetch(`${API}/api/intelligence/scrape-jobs?${queryString}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not load scrape jobs');
      setJobs(Array.isArray(body.items) ? body.items : []);
    } catch (err) {
      setJobs([]);
      setError(err.message || 'Could not load scrape jobs');
    } finally {
      setLoading(false);
    }
  }, [clientId, queryString, user?.role]);

  const loadReadiness = useCallback(async () => {
    if (!SCRAPE_GENERATE_SCRIPT_ENABLED || user?.role !== 'admin') {
      setReadiness(null);
      return;
    }
    setReadinessLoading(true);
    try {
      const headers = {};
      if (clientId) headers['X-Tenant-ID'] = clientId;
      const res = await fetch(`${API}/api/intelligence/readiness`, { headers });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not load readiness');
      setReadiness(body);
    } catch {
      setReadiness(null);
    } finally {
      setReadinessLoading(false);
    }
  }, [clientId, user?.role]);

  const loadLiveQa = useCallback(async () => {
    if (!SCRAPE_LIVE_QA_READINESS_ENABLED || user?.role !== 'admin') {
      setLiveQa(null);
      return;
    }
    setLiveQaLoading(true);
    try {
      const params = new URLSearchParams();
      if (clientId) params.set('clientId', clientId);
      const headers = {};
      if (clientId) headers['X-Tenant-ID'] = clientId;
      const res = await fetch(`${API}/api/intelligence/live-qa/readiness?${params.toString()}`, {
        headers,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not load live QA readiness');
      setLiveQa(body);
    } catch {
      setLiveQa(null);
    } finally {
      setLiveQaLoading(false);
    }
  }, [clientId, user?.role]);

  const loadGeneratedDraftQa = useCallback(async () => {
    if (!SCRAPE_GENERATED_DRAFT_QA_ENABLED || user?.role !== 'admin') {
      setGeneratedDraftQa(null);
      return;
    }
    setGeneratedDraftQaLoading(true);
    try {
      const params = new URLSearchParams();
      if (clientId) params.set('clientId', clientId);
      const headers = {};
      if (clientId) headers['X-Tenant-ID'] = clientId;
      const res = await fetch(`${API}/api/intelligence/generated-draft-qa/readiness?${params.toString()}`, {
        headers,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not load generated draft QA readiness');
      setGeneratedDraftQa(body);
    } catch {
      setGeneratedDraftQa(null);
    } finally {
      setGeneratedDraftQaLoading(false);
    }
  }, [clientId, user?.role]);

  const tenantHeaders = useCallback((json = false) => {
    const headers = json ? { 'Content-Type': 'application/json' } : {};
    if (clientId) headers['X-Tenant-ID'] = clientId;
    return headers;
  }, [clientId]);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (active) {
        loadJobs();
        loadReadiness();
        loadLiveQa();
        loadGeneratedDraftQa();
      }
    });
    return () => {
      active = false;
    };
  }, [loadJobs, loadReadiness, loadLiveQa, loadGeneratedDraftQa]);

  const openDiagnostics = async (job) => {
    if (!job?.id) return;
    setDetailsLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (clientId) params.set('clientId', clientId);
      const res = await fetch(`${API}/api/intelligence/scrape-jobs/${job.id}/diagnostics?${params.toString()}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not load diagnostics');
      setSelectedJob(body);
    } catch (err) {
      setError(err.message || 'Could not load diagnostics');
    } finally {
      setDetailsLoading(false);
    }
  };

  const dispatchJob = async (job) => {
    if (!canDispatchJob(job)) return;
    setJobActionKey(job.id);
    setError('');
    setNotice('');
    try {
      const res = await fetch(`${API}/api/intelligence/scrape-jobs/${job.id}/dispatch`, {
        method: 'POST',
        headers: tenantHeaders(true),
        body: JSON.stringify({
          requestedBy: user?.email || '',
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Worker dispatch failed');
      await loadJobs();
      const accepted = body.status === 'accepted';
      setNotice(accepted
        ? `Worker accepted for ${job.domain || job.id}. Refresh in a few seconds to see progress.`
        : `Worker is already ${String(body.status || 'queued').replaceAll('_', ' ')} for ${job.domain || job.id}.`);
      if (selectedJob?.id === job.id) {
        setSelectedJob({ ...selectedJob, status: accepted ? 'dispatching' : body.status, error: null });
      }
    } catch (err) {
      setError(err.message || 'Worker dispatch failed');
    } finally {
      setJobActionKey('');
    }
  };

  const cancelJob = async (job) => {
    if (!canCancelJob(job)) return;
    setJobActionKey(`cancel:${job.id}`);
    setError('');
    setNotice('');
    try {
      const res = await fetch(`${API}/api/intelligence/scrape-jobs/${job.id}/cancel`, {
        method: 'POST',
        headers: tenantHeaders(true),
        body: JSON.stringify({
          requestedBy: user?.email || '',
          reason: 'admin_cancelled',
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Cancel failed');
      await loadJobs();
      const updatedJob = body.job || { ...job, status: 'cancelled', error: 'admin_cancelled' };
      if (selectedJob?.id === job.id) {
        setSelectedJob({ ...selectedJob, ...updatedJob });
      }
      setNotice(`Cancelled scrape job for ${job.domain || job.id}. No live agent flow was changed.`);
    } catch (err) {
      setError(err.message || 'Cancel failed');
    } finally {
      setJobActionKey('');
    }
  };

  const recoverStaleJob = async (job) => {
    if (!canRecoverStaleJob(job)) return;
    setJobActionKey(`recover:${job.id}`);
    setError('');
    setNotice('');
    try {
      const res = await fetch(`${API}/api/intelligence/scrape-jobs/${job.id}/recover-stale`, {
        method: 'POST',
        headers: tenantHeaders(true),
        body: JSON.stringify({
          requestedBy: user?.email || '',
          reason: 'stale_worker_recovered',
          staleAfterMinutes: job.health?.stale_after_minutes || 15,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Stale recovery failed');
      await loadJobs();
      const updatedJob = body.job || { ...job, status: body.changed ? 'failed' : job.status };
      if (selectedJob?.id === job.id) {
        setSelectedJob({ ...selectedJob, ...updatedJob });
      }
      setNotice(body.changed
        ? `Recovered stale job for ${job.domain || job.id}. It is now marked failed and can be retried.`
        : `Job ${job.domain || job.id} is not stale enough to recover yet.`);
    } catch (err) {
      setError(err.message || 'Stale recovery failed');
    } finally {
      setJobActionKey('');
    }
  };

  const createDraftFromJob = async (job) => {
    if (!canCreateDraft(job)) return;
    setJobActionKey(`draft:${job.id}`);
    setError('');
    setNotice('');
    try {
      const industryHint = job.latest_extraction?.extraction?.industry || undefined;
      const res = await fetch(`${API}/api/intelligence/script-drafts`, {
        method: 'POST',
        headers: tenantHeaders(true),
        body: JSON.stringify({
          jobId: job.id,
          agentId: job.agent_id,
          industryHint,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Draft creation failed');
      const params = new URLSearchParams();
      if (clientId) params.set('clientId', clientId);
      const detailRes = await fetch(`${API}/api/intelligence/scrape-jobs/${job.id}/diagnostics?${params.toString()}`, {
        headers: tenantHeaders(),
      });
      const detailBody = await detailRes.json().catch(() => ({}));
      if (detailRes.ok) setSelectedJob(detailBody);
      await loadJobs();
      setNotice(`Review draft created for ${job.domain || job.id}. Live agent runtime is unchanged.`);
    } catch (err) {
      setError(err.message || 'Draft creation failed');
    } finally {
      setJobActionKey('');
    }
  };

  const closeFlowPreview = () => {
    setFlowPreviewAgent(null);
    setFlowPreview(null);
    setFlowPreviewLoading(false);
    setFlowPreviewError('');
    setFlowPreviewReadOnly(false);
  };

  const saveFlowDraft = async (draft) => {
    if (!flowPreviewAgent?.id) return;
    const res = await fetch(`${API}/api/agents/${flowPreviewAgent.id}/flow-v2-draft`, {
      method: 'PUT',
      headers: tenantHeaders(true),
      body: JSON.stringify(draft),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || 'Flow save failed');
    setFlowPreview(body);
  };

  const preflightGeneratedDraft = async (draft) => {
    if (!canSaveDraftToFlow(draft)) return;
    setJobActionKey(`preflight:${draft.id}`);
    setError('');
    setNotice('');
    setFlowPreviewLoading(true);
    setFlowPreviewError('');
    try {
      const res = await fetch(`${API}/api/intelligence/script-drafts/${draft.id}/preflight-flow-draft`, {
        method: 'POST',
        headers: tenantHeaders(),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Generated draft preflight failed');
      const agentId = body.agent?.id || draft.agent_id || selectedJob?.agent_id;
      setFlowPreviewAgent({ id: agentId, name: body.agent?.name || selectedJob?.agent_id || 'Voice Agent' });
      setFlowPreview(body);
      setFlowPreviewReadOnly(true);
      setNotice('Generated draft preflight passed. No flow draft was saved.');
    } catch (err) {
      setFlowPreviewError(err.message || 'Generated draft preflight failed');
      setError(err.message || 'Generated draft preflight failed');
    } finally {
      setFlowPreviewLoading(false);
      setJobActionKey('');
    }
  };

  const saveGeneratedDraftToFlow = async (draft) => {
    if (!canSaveDraftToFlow(draft)) return;
    setJobActionKey(`flow:${draft.id}`);
    setError('');
    setNotice('');
    setFlowPreviewLoading(true);
    setFlowPreviewError('');
    try {
      const res = await fetch(`${API}/api/intelligence/script-drafts/${draft.id}/apply-flow-draft`, {
        method: 'POST',
        headers: tenantHeaders(true),
        body: JSON.stringify({
          reviewAcknowledged: true,
          reviewNotes: 'Saved from intelligence generated draft review',
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Generated draft could not be saved to flow');
      const agentId = body.agent?.id || draft.agent_id || selectedJob?.agent_id;
      setFlowPreviewAgent({ id: agentId, name: body.agent?.name || selectedJob?.agent_id || 'Voice Agent' });
      setFlowPreview(body);
      setFlowPreviewReadOnly(false);
      setSelectedJob(null);
      setNotice('Review recorded. Generated draft saved to Flow V2 draft. Live calls and published runtime are unchanged.');
      await loadJobs();
    } catch (err) {
      setFlowPreviewError(err.message || 'Generated draft could not be saved to flow');
      setError(err.message || 'Generated draft could not be saved to flow');
    } finally {
      setFlowPreviewLoading(false);
      setJobActionKey('');
    }
  };

  if (user?.role !== 'admin') {
    return (
      <DashboardLayout>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <h2 className="h5 fw-bold text-dark mb-2">Access restricted</h2>
            <p className="small mb-0">Website intelligence diagnostics are available only to platform admins.</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  if (!SCRAPE_GENERATE_SCRIPT_ENABLED) {
    return (
      <DashboardLayout>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <h2 className="h5 fw-bold text-dark mb-2">Website intelligence is disabled</h2>
            <p className="small mb-0">Enable the scrape.generate_script rollout flag to view generated-script jobs.</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Website Intelligence</h2>
          <p className="text-muted small mb-0">Client: {clientId || 'All clients'}</p>
        </div>
        <div className="d-flex gap-2">
          <select className="form-select form-select-sm" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="dispatching">Dispatching</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="draft_ready">Draft ready</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <button type="button" className="btn btn-primary btn-sm px-3" onClick={loadJobs} disabled={loading}>
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && <div className="alert alert-warning py-2 small">{error}</div>}
      {notice && <div className="alert alert-success py-2 small">{notice}</div>}

      <div className="card border-0 shadow-sm mb-4">
        <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
          <h6 className="mb-0 fw-bold">Production Readiness</h6>
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={loadReadiness} disabled={readinessLoading}>
            {readinessLoading ? 'Checking...' : 'Check'}
          </button>
        </div>
        <div className="card-body">
          {readiness ? (
            <div className="row g-3">
              <div className="col-md-3">
                <div className="border rounded p-3 h-100">
                  <div className="text-muted small">Status</div>
                  <div className="fw-bold text-capitalize">{readiness.status || 'unknown'}</div>
                </div>
              </div>
              <div className="col-md-3">
                <div className="border rounded p-3 h-100">
                  <div className="text-muted small">Crawler</div>
                  <div className="fw-bold">{readiness.crawler_provider || '-'}</div>
                </div>
              </div>
              <div className="col-md-3">
                <div className="border rounded p-3 h-100">
                  <div className="text-muted small">Pages / Bytes</div>
                  <div className="fw-bold">{readiness.limits?.max_pages || '-'} / {readiness.limits?.max_bytes || '-'}</div>
                </div>
              </div>
              <div className="col-md-3">
                <div className="border rounded p-3 h-100">
                  <div className="text-muted small">Blockers</div>
                  <div className="fw-bold">{readiness.blockers?.length || 0}</div>
                </div>
              </div>
              <div className="col-12">
                <div className="d-flex flex-wrap gap-2">
                  {Object.entries(readiness.flags || {}).map(([flag, config]) => (
                    <span key={flag} className={`badge border ${config.enabled ? 'bg-success-subtle text-success border-success-subtle' : 'bg-secondary-subtle text-secondary border-secondary-subtle'}`}>
                      {flag}: {config.enabled ? 'on' : 'off'}
                    </span>
                  ))}
                </div>
              </div>
              {readiness.blockers?.length ? (
                <div className="col-12">
                  <div className="alert alert-warning py-2 small mb-0">
                    {readiness.blockers.slice(0, 5).join(', ')}
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="text-muted small">{readinessLoading ? 'Checking readiness...' : 'Readiness snapshot not loaded.'}</div>
          )}
        </div>
      </div>

      {SCRAPE_LIVE_QA_READINESS_ENABLED && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
            <div>
              <h6 className="mb-0 fw-bold">Live Scrape QA</h6>
              <div className="text-muted small">Real-URL evidence before production push.</div>
            </div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={loadLiveQa} disabled={liveQaLoading}>
              {liveQaLoading ? 'Checking...' : 'Check'}
            </button>
          </div>
          <div className="card-body">
            {liveQa ? (
              <div className="row g-3">
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Status</div>
                    <div className="fw-bold text-capitalize">{liveQa.status || 'unknown'}</div>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Real Domains</div>
                    <div className="fw-bold">{liveQa.summary?.production_domains ?? 0}</div>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Ready Samples</div>
                    <div className="fw-bold">{liveQa.summary?.ready_samples ?? 0}</div>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Blockers</div>
                    <div className="fw-bold">{liveQa.blockers?.length || 0}</div>
                  </div>
                </div>
                <div className="col-lg-6">
                  <div className="border rounded p-3 h-100">
                    <div className="fw-semibold mb-2">QA Criteria</div>
                    <div className="d-flex flex-column gap-2 small">
                      {(liveQa.criteria || []).map((item) => (
                        <div key={item.key} className="d-flex justify-content-between gap-3">
                          <span>{item.label}</span>
                          <span className={item.passed ? 'text-success fw-semibold' : 'text-warning fw-semibold'}>
                            {item.passed ? 'Pass' : 'Review'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="col-lg-6">
                  <div className="border rounded p-3 h-100">
                    <div className="fw-semibold mb-2">Recent QA Samples</div>
                    {(liveQa.samples || []).length ? (
                      <div className="d-flex flex-column gap-2 small">
                        {liveQa.samples.slice(0, 5).map((sample) => (
                          <div key={sample.job_id} className="d-flex justify-content-between gap-3">
                            <span className="text-truncate">{sample.domain}</span>
                            <span className="text-capitalize">{sample.quality_level || 'unknown'} / {sample.pages || 0} pages</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-muted small">No real-URL QA samples yet.</div>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-muted small">{liveQaLoading ? 'Checking live QA evidence...' : 'Live QA readiness snapshot not loaded.'}</div>
            )}
          </div>
        </div>
      )}

      {SCRAPE_GENERATED_DRAFT_QA_ENABLED && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
            <div>
              <h6 className="mb-0 fw-bold">Generated Draft QA</h6>
              <div className="text-muted small">Review, edit, and save evidence before production push.</div>
            </div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={loadGeneratedDraftQa} disabled={generatedDraftQaLoading}>
              {generatedDraftQaLoading ? 'Checking...' : 'Check'}
            </button>
          </div>
          <div className="card-body">
            {generatedDraftQa ? (
              <div className="row g-3">
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Status</div>
                    <div className="fw-bold text-capitalize">{generatedDraftQa.status || 'unknown'}</div>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Generated</div>
                    <div className="fw-bold">{generatedDraftQa.summary?.generated_drafts ?? 0}</div>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Saved</div>
                    <div className="fw-bold">{generatedDraftQa.summary?.reviewed_saved_drafts ?? 0}</div>
                  </div>
                </div>
                <div className="col-md-3">
                  <div className="border rounded p-3 h-100">
                    <div className="text-muted small">Blockers</div>
                    <div className="fw-bold">{generatedDraftQa.blockers?.length || 0}</div>
                  </div>
                </div>
                <div className="col-lg-6">
                  <div className="border rounded p-3 h-100">
                    <div className="fw-semibold mb-2">QA Criteria</div>
                    <div className="d-flex flex-column gap-2 small">
                      {(generatedDraftQa.criteria || []).map((item) => (
                        <div key={item.key} className="d-flex justify-content-between gap-3">
                          <span>{item.label}</span>
                          <span className={item.passed ? 'text-success fw-semibold' : 'text-warning fw-semibold'}>
                            {item.passed ? 'Pass' : 'Review'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="col-lg-6">
                  <div className="border rounded p-3 h-100">
                    <div className="fw-semibold mb-2">Saved Flow Drafts</div>
                    {(generatedDraftQa.samples || []).length ? (
                      <div className="d-flex flex-column gap-2 small">
                        {generatedDraftQa.samples.slice(0, 5).map((sample) => (
                          <div key={sample.draft_id} className="d-flex justify-content-between gap-3">
                            <span className="text-truncate">{sample.domain || sample.draft_id}</span>
                            <span className={sample.flow_artifact_valid ? 'text-success' : 'text-warning'}>
                              {sample.flow_artifact_valid ? 'artifact valid' : sample.status || 'draft'}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-muted small">No generated draft QA samples yet.</div>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-muted small">{generatedDraftQaLoading ? 'Checking generated draft QA evidence...' : 'Generated draft QA snapshot not loaded.'}</div>
            )}
          </div>
        </div>
      )}

      <div className="card border-0 shadow-sm">
        <div className="card-header bg-white border-bottom py-3">
          <h6 className="mb-0 fw-bold">Scrape Jobs</h6>
        </div>
        <div className="card-body p-0">
          {loading ? (
            <div className="p-4 text-muted small">Loading jobs...</div>
          ) : jobs.length === 0 ? (
            <div className="p-4 text-muted small">No scrape jobs found.</div>
          ) : (
            <div className="table-responsive">
              <table className="table table-hover align-middle mb-0">
                <thead className="table-light text-muted small">
                  <tr>
                    <th className="px-4 py-3">Website</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Agent</th>
                    <th className="px-4 py-3">Pages</th>
                    <th className="px-4 py-3">Drafts</th>
                    <th className="px-4 py-3">Updated</th>
                    <th className="px-4 py-3 text-end">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.id}>
                      <td className="px-4 py-3">
                        <div className="fw-semibold">{job.domain}</div>
                        <div className="text-muted small text-truncate" style={{ maxWidth: '320px' }}>{job.url}</div>
                        {job.error && <div className="text-danger small mt-1">{job.error}</div>}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge border text-capitalize ${statusClass(job.status)}`}>{job.status || 'queued'}</span>
                        {job.health?.is_stale && <span className="badge bg-warning-subtle text-warning border border-warning-subtle ms-2">Stale</span>}
                      </td>
                      <td className="px-4 py-3 small">{job.agent_id || '-'}</td>
                      <td className="px-4 py-3 small">{job.diagnostics?.page_count ?? 0}</td>
                      <td className="px-4 py-3 small">{job.diagnostics?.draft_count ?? 0}</td>
                      <td className="px-4 py-3 small">{formatDate(job.updated_at || job.created_at)}</td>
                      <td className="px-4 py-3 text-end">
                        <div className="d-flex justify-content-end gap-2">
                          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => openDiagnostics(job)} disabled={detailsLoading}>
                            Details
                          </button>
                          {canDispatchJob(job) && (
                            <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => dispatchJob(job)} disabled={jobActionKey === job.id}>
                              {jobActionKey === job.id ? 'Dispatching...' : 'Retry Worker'}
                            </button>
                          )}
                          {canRecoverStaleJob(job) && (
                            <button type="button" className="btn btn-outline-warning btn-sm" onClick={() => recoverStaleJob(job)} disabled={jobActionKey === `recover:${job.id}`}>
                              {jobActionKey === `recover:${job.id}` ? 'Recovering...' : 'Recover'}
                            </button>
                          )}
                          {canCancelJob(job) && (
                            <button type="button" className="btn btn-outline-danger btn-sm" onClick={() => cancelJob(job)} disabled={jobActionKey === `cancel:${job.id}`}>
                              {jobActionKey === `cancel:${job.id}` ? 'Cancelling...' : 'Cancel'}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {selectedJob && (
        <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
          <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
            <div className="modal-content border-0 shadow">
              <div className="modal-header">
                <div>
                  <h5 className="modal-title fw-bold mb-1">Scrape Diagnostics</h5>
                  <div className="text-muted small">{selectedJob.domain} - {(selectedJob.id || '').substring(0, 8)}</div>
                </div>
                <button type="button" className="btn-close shadow-none" onClick={() => setSelectedJob(null)}></button>
              </div>
              <div className="modal-body bg-light">
                <div className="row g-3 mb-3">
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Status</div>
                      <div className="fw-bold text-capitalize">{selectedJob.status || 'queued'}</div>
                    </div>
                  </div>
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Pages</div>
                      <div className="fw-bold">{selectedJob.diagnostics?.page_count ?? 0}</div>
                    </div>
                  </div>
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Drafts</div>
                      <div className="fw-bold">{selectedJob.diagnostics?.draft_count ?? 0}</div>
                    </div>
                  </div>
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Completed</div>
                      <div className="fw-bold small">{formatDate(selectedJob.completed_at)}</div>
                    </div>
                  </div>
                </div>

                {selectedJob.error && <div className="alert alert-danger py-2 small">{selectedJob.error}</div>}
                {selectedJob.health?.is_stale && (
                  <div className="alert alert-warning py-2 small">
                    This scrape job has been stuck for {selectedJob.health.age_minutes} minutes.
                  </div>
                )}
                {canDispatchJob(selectedJob) && (
                  <div className="border rounded bg-white p-3 mb-3 d-flex justify-content-between align-items-center gap-3">
                    <div>
                      <div className="fw-bold">Worker retry</div>
                      <div className="text-muted small">Re-dispatches this scrape job without changing any live agent flow.</div>
                    </div>
                    <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => dispatchJob(selectedJob)} disabled={jobActionKey === selectedJob.id}>
                      {jobActionKey === selectedJob.id ? 'Dispatching...' : 'Retry Worker'}
                    </button>
                  </div>
                )}
                {canRecoverStaleJob(selectedJob) && (
                  <div className="border rounded bg-white p-3 mb-3 d-flex justify-content-between align-items-center gap-3">
                    <div>
                      <div className="fw-bold">Recover stale job</div>
                      <div className="text-muted small">Marks stuck work as failed so the scrape can be retried safely.</div>
                    </div>
                    <button type="button" className="btn btn-outline-warning btn-sm" onClick={() => recoverStaleJob(selectedJob)} disabled={jobActionKey === `recover:${selectedJob.id}`}>
                      {jobActionKey === `recover:${selectedJob.id}` ? 'Recovering...' : 'Recover'}
                    </button>
                  </div>
                )}
                {canCancelJob(selectedJob) && (
                  <div className="border rounded bg-white p-3 mb-3 d-flex justify-content-between align-items-center gap-3">
                    <div>
                      <div className="fw-bold">Cancel job</div>
                      <div className="text-muted small">Stops queued work and asks a running scrape to exit safely. Live calls and drafts are unchanged.</div>
                    </div>
                    <button type="button" className="btn btn-outline-danger btn-sm" onClick={() => cancelJob(selectedJob)} disabled={jobActionKey === `cancel:${selectedJob.id}`}>
                      {jobActionKey === `cancel:${selectedJob.id}` ? 'Cancelling...' : 'Cancel Job'}
                    </button>
                  </div>
                )}
                {canCreateDraft(selectedJob) && (
                  <div className="border rounded bg-white p-3 mb-3 d-flex justify-content-between align-items-center gap-3">
                    <div>
                      <div className="fw-bold">Create review draft</div>
                      <div className="text-muted small">Creates a generated script draft for admin review only. Nothing is published.</div>
                    </div>
                    <button type="button" className="btn btn-outline-success btn-sm" onClick={() => createDraftFromJob(selectedJob)} disabled={jobActionKey === `draft:${selectedJob.id}`}>
                      {jobActionKey === `draft:${selectedJob.id}` ? 'Creating...' : 'Create Draft'}
                    </button>
                  </div>
                )}
                {hasGeneratedDraft(selectedJob) && (
                  <div className="border rounded bg-white p-3 mb-3">
                    <div className="fw-bold">Draft already exists</div>
                    <div className="text-muted small">Use the generated draft below instead of creating another copy for this scrape job.</div>
                  </div>
                )}

                <div className="row g-3">
                  <div className="col-lg-6">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="fw-bold mb-2">Latest Extraction</div>
                      {selectedJob.latest_extraction?.extraction ? (
                        <div className="small">
                          {extractionQuality(selectedJob) && (
                            <div className="d-flex align-items-center gap-2 mb-2">
                              <span className={`badge border text-capitalize ${qualityClass(extractionQuality(selectedJob).level)}`}>
                                {extractionQuality(selectedJob).level} readiness
                              </span>
                              <span className="text-muted">Score {extractionQuality(selectedJob).score}/100</span>
                            </div>
                          )}
                          <div><span className="text-muted">Business:</span> {selectedJob.latest_extraction.extraction.company?.name || selectedJob.domain}</div>
                          <div><span className="text-muted">Industry:</span> {String(selectedJob.latest_extraction.extraction.industry || 'unknown').replaceAll('_', ' ')}</div>
                          {contentInventoryItems(selectedJob.latest_extraction.extraction).length > 0 && (
                            <div className="border-top mt-3 pt-2">
                              <div className="text-muted mb-1">Content Inventory</div>
                              <div className="d-flex flex-wrap gap-1">
                                {contentInventoryItems(selectedJob.latest_extraction.extraction).map(([pageType, count]) => (
                                  <span key={pageType} className="badge bg-light text-dark border text-capitalize">
                                    {pageType.replaceAll('_', ' ')}: {count}
                                  </span>
                                ))}
                                {selectedJob.latest_extraction.extraction.content_inventory?.noise_filtered && (
                                  <span className="badge bg-success-subtle text-success border border-success-subtle">Noise filtered</span>
                                )}
                              </div>
                            </div>
                          )}
                          <div className="text-muted mt-2">Services</div>
                          <div className="d-flex flex-wrap gap-1">
                            {(selectedJob.latest_extraction.extraction.products_or_services || []).slice(0, 8).map((item, index) => (
                              <span key={`${item.name}-${index}`} className="badge bg-light text-dark border">{item.name}</span>
                            ))}
                          </div>
                          <ConversationGuidance knowledge={selectedJob.latest_extraction.extraction} />
                          {extractionQuality(selectedJob)?.warnings?.length ? (
                            <div className="border-top mt-3 pt-2">
                              <div className="text-muted mb-1">Review warnings</div>
                              <ul className="mb-0 ps-3">
                                {extractionQuality(selectedJob).warnings.slice(0, 4).map((warning, index) => (
                                  <li key={`${warning}-${index}`}>{warning}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {sourceEvidenceFromKnowledge(selectedJob.latest_extraction.extraction).length > 0 && (
                            <div className="border-top mt-3 pt-2">
                              <div className="text-muted mb-1">Source Evidence</div>
                              <div className="d-flex flex-column gap-1">
                                {sourceEvidenceFromKnowledge(selectedJob.latest_extraction.extraction).map((url) => (
                                  <a key={url} href={url} target="_blank" rel="noreferrer" className="text-truncate">
                                    {url}
                                  </a>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="text-muted small">No extraction stored yet.</div>
                      )}
                    </div>
                  </div>
                  <div className="col-lg-6">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="fw-bold mb-2">Generated Drafts</div>
                      {selectedJob.drafts?.length ? (
                        <div className="d-flex flex-column gap-2">
                          {selectedJob.drafts.map((draft) => (
                            <div key={draft.id} className="border rounded p-2 small d-flex justify-content-between align-items-center gap-3">
                              <div>
                                <div className="fw-semibold">{(draft.id || '').substring(0, 8)} - {draft.status || 'draft'}</div>
                                <div className="text-muted">{formatDate(draft.created_at)}</div>
                                {draft.reviewed_at && (
                                  <div className="text-success">Saved to Flow Draft {formatDate(draft.reviewed_at)}</div>
                                )}
                              </div>
                              {canSaveDraftToFlow(draft) && (
                                <div className="d-flex gap-2">
                                  <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => preflightGeneratedDraft(draft)} disabled={jobActionKey === `preflight:${draft.id}`}>
                                    {jobActionKey === `preflight:${draft.id}` ? 'Checking...' : 'Preflight'}
                                  </button>
                                  <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => saveGeneratedDraftToFlow(draft)} disabled={jobActionKey === `flow:${draft.id}`}>
                                    {jobActionKey === `flow:${draft.id}` ? 'Saving...' : 'Save to Flow Draft'}
                                  </button>
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-muted small">No generated drafts yet.</div>
                      )}
                    </div>
                  </div>
                  <div className="col-12">
                    <div className="border rounded bg-white p-3">
                      <div className="fw-bold mb-2">Page Snapshots</div>
                      {selectedJob.snapshots?.length ? (
                        <div className="table-responsive">
                          <table className="table table-sm align-middle mb-0">
                            <thead className="table-light small text-muted">
                              <tr>
                                <th>URL</th>
                                <th>Content Type</th>
                                <th>Hash</th>
                              </tr>
                            </thead>
                            <tbody>
                              {selectedJob.snapshots.map((snapshot) => (
                                <tr key={snapshot.id}>
                                  <td className="text-truncate" style={{ maxWidth: '520px' }}>{snapshot.url}</td>
                                  <td>{snapshot.content_type || '-'}</td>
                                  <td className="small text-muted">{snapshot.content_hash || '-'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="text-muted small">No page snapshots stored yet.</div>
                      )}
                    </div>
                  </div>
                  {SCRAPE_JOB_EVENTS_ENABLED && (
                  <div className="col-12">
                    <div className="border rounded bg-white p-3">
                      <div className="fw-bold mb-2">Job Events</div>
                      {selectedJob.events?.length ? (
                        <div className="table-responsive">
                          <table className="table table-sm align-middle mb-0">
                            <thead className="table-light small text-muted">
                              <tr>
                                <th>Time</th>
                                <th>Event</th>
                                <th>Status</th>
                                <th>Actor</th>
                              </tr>
                            </thead>
                            <tbody>
                              {selectedJob.events.map((event) => (
                                <tr key={event.id}>
                                  <td className="small text-muted">{formatDate(event.created_at)}</td>
                                  <td className="text-capitalize">{formatEventType(event.event_type)}</td>
                                  <td>
                                    <span className={`badge border text-capitalize ${statusClass(event.status)}`}>{event.status || '-'}</span>
                                  </td>
                                  <td className="small">{event.actor || '-'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="text-muted small">No job events recorded yet.</div>
                      )}
                    </div>
                  </div>
                  )}
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-light border" onClick={() => setSelectedJob(null)}>Close</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {flowPreviewAgent && (
        <FlowPreviewModal
          agent={flowPreviewAgent}
          preview={flowPreview}
          loading={flowPreviewLoading}
          error={flowPreviewError}
          onClose={closeFlowPreview}
          onSave={flowPreviewReadOnly ? undefined : saveFlowDraft}
        />
      )}
    </DashboardLayout>
  );
}
