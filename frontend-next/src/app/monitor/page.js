'use client';
import { useCallback, useEffect, useState, useRef } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth } from '@/context/AuthContext';
import { formatProviderMetricKey, getProviderLabel } from '@/lib/providerDisplay';

const MONITOR_AUTH_PROOF_ENABLED = process.env.NEXT_PUBLIC_MONITOR_AUTH_PROOF_ENABLED === 'true';
const DEMO_RUNTIME_QA_READINESS_ENABLED = process.env.NEXT_PUBLIC_DEMO_RUNTIME_QA_READINESS_ENABLED === 'true';
const TENANT_SECURITY_AUDIT_ENABLED = process.env.NEXT_PUBLIC_TENANT_SECURITY_AUDIT_ENABLED === 'true';
const FINAL_CANARY_ROLLBACK_ENABLED = process.env.NEXT_PUBLIC_FINAL_CANARY_ROLLBACK_ENABLED === 'true';

async function getAdminIdToken(firebaseUser, currentRole) {
  if (currentRole !== 'admin' || !firebaseUser || typeof firebaseUser.getIdToken !== 'function') return null;
  try {
    return await firebaseUser.getIdToken();
  } catch (error) {
    console.error('Monitor auth token unavailable', error);
    return null;
  }
}

function adminAuthHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function monitorWsUrl(apiBase, token) {
  const base = apiBase.replace(/^https/, 'wss').replace(/^http/, 'ws').replace(/\/$/, '');
  const url = new URL(`${base}/ws/dashboard`);
  if (MONITOR_AUTH_PROOF_ENABLED && token) {
    url.searchParams.set('access_token', token);
  }
  return url.toString();
}

export default function AdminMonitor() {
  const { currentRole, loading, firebaseUser, activeClient } = useAuth();
  const [metrics, setMetrics] = useState({
    totalCalls: 0,
    activeAgents: 0,
    connectRate: 0,
    sysLoad: 'Normal'
  });
  const [liveCalls, setLiveCalls] = useState({});
  const [providerMetrics, setProviderMetrics] = useState({});
  const [demoQa, setDemoQa] = useState(null);
  const [demoQaLoading, setDemoQaLoading] = useState(false);
  const [securityAudit, setSecurityAudit] = useState(null);
  const [securityAuditLoading, setSecurityAuditLoading] = useState(false);
  const [finalGate, setFinalGate] = useState(null);
  const [finalGateLoading, setFinalGateLoading] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    if (loading || currentRole !== 'admin') return undefined;

    let cancelled = false;
    const API = process.env.NEXT_PUBLIC_API_URL || `http://localhost:8000`;

    const connectMonitorSocket = async () => {
      const token = await getAdminIdToken(firebaseUser, currentRole);
      if (cancelled) return;

      const socket = new WebSocket(monitorWsUrl(API, token));
      wsRef.current = socket;

      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'dashboard_update') {
            const callsMap = {};
            let activeCnt = 0;
            let connectedCnt = 0;

            msg.calls.forEach(call => {
              callsMap[call.lead_id] = call;
              if (['ringing', 'connected', 'talking'].includes(call.status)) activeCnt++;
              if (['connected', 'talking'].includes(call.status)) connectedCnt++;
            });

            setLiveCalls(callsMap);
            setMetrics({
              totalCalls: msg.calls.length,
              activeAgents: activeCnt,
              connectRate: msg.calls.length > 0 ? Math.round((connectedCnt / msg.calls.length) * 100) : 0,
              sysLoad: 'Normal'
            });
          } else if (msg.type?.startsWith('call_')) {
            setLiveCalls(prev => {
              const up = { ...prev };
              if (!up[msg.leadId]) up[msg.leadId] = { lead_id: msg.leadId };

              up[msg.leadId].status = msg.type.replace('call_', '');
              up[msg.leadId].lead_name = msg.leadName || up[msg.leadId].lead_name;
              up[msg.leadId].provider = msg.provider || up[msg.leadId].provider;

              if (msg.type === 'call_talking') {
                up[msg.leadId].last_snippet = msg.snippet;
              }
              if (msg.type === 'call_completed') {
                up[msg.leadId].result = msg.result;
              }
              return up;
            });
          }
        } catch (e) {
          console.error("Dashboard WS parse error", e);
        }
      };

      socket.onclose = (event) => {
        if (event.code === 1008) {
          console.error('Dashboard WS closed by authorization policy');
        }
      };
    };

    connectMonitorSocket();

    return () => {
      cancelled = true;
      if (wsRef.current && [WebSocket.CONNECTING, WebSocket.OPEN].includes(wsRef.current.readyState)) {
        wsRef.current.close();
      }
      wsRef.current = null;
    };
  }, [currentRole, loading, firebaseUser]);

  useEffect(() => {
    if (loading || currentRole !== 'admin') return undefined;

    const API = process.env.NEXT_PUBLIC_API_URL || `http://localhost:8000`;
    let cancelled = false;

    const fetchProviderMetrics = async () => {
      try {
        const token = await getAdminIdToken(firebaseUser, currentRole);
        const res = await fetch(`${API}/api/provider-metrics`, {
          headers: adminAuthHeaders(token),
        });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setProviderMetrics(data.metrics || {});
      } catch (e) {
        console.error('Provider metrics fetch error', e);
      }
    };

    fetchProviderMetrics();
    const interval = setInterval(fetchProviderMetrics, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [currentRole, loading, firebaseUser]);

  const fetchDemoQaReadiness = useCallback(async () => {
    if (!DEMO_RUNTIME_QA_READINESS_ENABLED || currentRole !== 'admin') return;

    const API = process.env.NEXT_PUBLIC_API_URL || `http://localhost:8000`;
    setDemoQaLoading(true);
    try {
      const token = await getAdminIdToken(firebaseUser, currentRole);
      const res = await fetch(`${API}/api/demo/qa/readiness`, {
        headers: adminAuthHeaders(token),
      });
      if (!res.ok) {
        setDemoQa({
          status: 'disabled',
          blockers: [`HTTP ${res.status}`],
          criteria: [],
        });
        return;
      }
      const data = await res.json();
      setDemoQa(data);
    } catch (e) {
      console.error('Demo QA readiness fetch error', e);
      setDemoQa({
        status: 'unavailable',
        blockers: ['request_failed'],
        criteria: [],
      });
    } finally {
      setDemoQaLoading(false);
    }
  }, [currentRole, firebaseUser]);

  const fetchSecurityAuditReadiness = useCallback(async () => {
    if (!TENANT_SECURITY_AUDIT_ENABLED || currentRole !== 'admin') return;

    const API = process.env.NEXT_PUBLIC_API_URL || `http://localhost:8000`;
    const params = new URLSearchParams();
    if (activeClient) params.set('clientId', activeClient);
    const query = params.toString() ? `?${params.toString()}` : '';
    setSecurityAuditLoading(true);
    try {
      const token = await getAdminIdToken(firebaseUser, currentRole);
      const res = await fetch(`${API}/api/tenant/security-leak-audit/readiness${query}`, {
        headers: adminAuthHeaders(token),
      });
      if (!res.ok) {
        setSecurityAudit({
          status: 'disabled',
          blockers: [`HTTP ${res.status}`],
          criteria: [],
        });
        return;
      }
      const data = await res.json();
      setSecurityAudit(data);
    } catch (e) {
      console.error('Tenant security audit fetch error', e);
      setSecurityAudit({
        status: 'unavailable',
        blockers: ['request_failed'],
        criteria: [],
      });
    } finally {
      setSecurityAuditLoading(false);
    }
  }, [activeClient, currentRole, firebaseUser]);

  const fetchFinalGateReadiness = useCallback(async () => {
    if (!FINAL_CANARY_ROLLBACK_ENABLED || currentRole !== 'admin') return;

    const API = process.env.NEXT_PUBLIC_API_URL || `http://localhost:8000`;
    const params = new URLSearchParams();
    if (activeClient) params.set('clientId', activeClient);
    const query = params.toString() ? `?${params.toString()}` : '';
    setFinalGateLoading(true);
    try {
      const token = await getAdminIdToken(firebaseUser, currentRole);
      const res = await fetch(`${API}/api/tenant/final-canary-rollback/readiness${query}`, {
        headers: adminAuthHeaders(token),
      });
      if (!res.ok) {
        setFinalGate({
          status: 'disabled',
          blockers: [`HTTP ${res.status}`],
          criteria: [],
        });
        return;
      }
      const data = await res.json();
      setFinalGate(data);
    } catch (e) {
      console.error('Final canary rollback readiness fetch error', e);
      setFinalGate({
        status: 'unavailable',
        blockers: ['request_failed'],
        criteria: [],
      });
    } finally {
      setFinalGateLoading(false);
    }
  }, [activeClient, currentRole, firebaseUser]);

  useEffect(() => {
    if (loading || currentRole !== 'admin' || !DEMO_RUNTIME_QA_READINESS_ENABLED) return undefined;

    let cancelled = false;
    const load = async () => {
      if (!cancelled) await fetchDemoQaReadiness();
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [currentRole, loading, firebaseUser, fetchDemoQaReadiness]);

  useEffect(() => {
    if (loading || currentRole !== 'admin' || !TENANT_SECURITY_AUDIT_ENABLED) return undefined;

    let cancelled = false;
    const load = async () => {
      if (!cancelled) await fetchSecurityAuditReadiness();
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [currentRole, loading, firebaseUser, fetchSecurityAuditReadiness]);

  useEffect(() => {
    if (loading || currentRole !== 'admin' || !FINAL_CANARY_ROLLBACK_ENABLED) return undefined;

    let cancelled = false;
    const load = async () => {
      if (!cancelled) await fetchFinalGateReadiness();
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [currentRole, loading, firebaseUser, fetchFinalGateReadiness]);

  const getStatusBadge = (status) => {
    switch (status) {
      case 'ringing': return <span className="badge bg-warning text-dark"><span className="spinner-grow spinner-grow-sm me-1" aria-hidden="true"></span>Ringing</span>;
      case 'connected': return <span className="badge bg-success">Connected</span>;
      case 'talking': return <span className="badge bg-primary"><span className="spinner-grow spinner-grow-sm me-1" aria-hidden="true"></span>Talking</span>;
      case 'completed': return <span className="badge bg-secondary">Completed</span>;
      default: return <span className="badge bg-light text-dark">{status}</span>;
    }
  };

  if (!loading && currentRole !== 'admin') {
    return (
      <DashboardLayout>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-4">
            <h2 className="h5 fw-bold mb-2">Admin Access Required</h2>
            <p className="text-muted mb-0">Live Monitor is available only to platform admins.</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 mb-1 fw-bold">Live Monitor</h2>
          <p className="text-muted small mb-0">Real-time system oversight and active call tracking</p>
        </div>
        <div className="d-flex gap-2">
          <span className="badge bg-success-subtle text-success border border-success-subtle d-flex align-items-center gap-1 px-3 py-2">
            <span className="spinner-grow spinner-grow-sm" style={{ width: '8px', height: '8px' }}></span>
            System Online
          </span>
        </div>
      </div>

      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <div className="card border-0 shadow-sm">
            <div className="card-body">
              <h6 className="text-muted small text-uppercase fw-semibold mb-1">Total Dispatched</h6>
              <h3 className="mb-0 fw-bold">{metrics.totalCalls}</h3>
            </div>
          </div>
        </div>
        <div className="col-md-3">
          <div className="card border-0 shadow-sm">
            <div className="card-body">
              <h6 className="text-muted small text-uppercase fw-semibold mb-1">Active Agents</h6>
              <h3 className="mb-0 fw-bold text-primary">{metrics.activeAgents}</h3>
            </div>
          </div>
        </div>
        <div className="col-md-3">
          <div className="card border-0 shadow-sm">
            <div className="card-body">
              <h6 className="text-muted small text-uppercase fw-semibold mb-1">Connect Rate</h6>
              <h3 className="mb-0 fw-bold text-success">{metrics.connectRate}%</h3>
            </div>
          </div>
        </div>
        <div className="col-md-3">
          <div className="card border-0 shadow-sm">
            <div className="card-body">
              <h6 className="text-muted small text-uppercase fw-semibold mb-1">System Load</h6>
              <h3 className="mb-0 fw-bold">{metrics.sysLoad}</h3>
            </div>
          </div>
        </div>
      </div>

      {FINAL_CANARY_ROLLBACK_ENABLED && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
            <div>
              <h6 className="mb-0 fw-bold">Final Canary Gate</h6>
              <div className="small text-muted">Manual canary, rollback, isolation, and production push readiness</div>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-outline-primary"
              onClick={fetchFinalGateReadiness}
              disabled={finalGateLoading}
            >
              {finalGateLoading ? 'Checking...' : 'Check now'}
            </button>
          </div>
          <div className="card-body">
            {!finalGate ? (
              <p className="text-muted small mb-0">No final canary snapshot loaded yet.</p>
            ) : (
              <>
                <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                  <span className={`badge ${finalGate.status === 'ready' ? 'bg-success' : 'bg-warning text-dark'}`}>
                    {finalGate.status}
                  </span>
                  <span className="small text-muted">Mode: {finalGate.mode || 'read_only'}</span>
                  {finalGate.canary_started === false && (
                    <span className="badge bg-light text-dark border">No canary started</span>
                  )}
                  {finalGate.traffic_shifted === false && (
                    <span className="badge bg-light text-dark border">No traffic shifted</span>
                  )}
                  {finalGate.rollback_action_performed === false && (
                    <span className="badge bg-light text-dark border">No rollback executed</span>
                  )}
                  {finalGate.feature_flags_modified === false && (
                    <span className="badge bg-light text-dark border">No flag changes</span>
                  )}
                  {finalGate.audio_runtime_changed === false && (
                    <span className="badge bg-light text-dark border">Audio unchanged</span>
                  )}
                </div>
                {finalGate.blockers?.length > 0 && (
                  <div className="alert alert-warning py-2 small mb-3">
                    Blockers: {finalGate.blockers.join(', ')}
                  </div>
                )}
                {finalGate.summary && (
                  <div className="row g-2 mb-3">
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Campaign ready</div>
                        <div className="fw-bold">{finalGate.summary.campaign_ready ? 'Yes' : 'No'}</div>
                      </div>
                    </div>
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Security ready</div>
                        <div className="fw-bold">{finalGate.summary.tenant_security_ready ? 'Yes' : 'No'}</div>
                      </div>
                    </div>
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Observation window</div>
                        <div className="fw-bold">{finalGate.summary.minimum_observation_minutes ?? 0} min</div>
                      </div>
                    </div>
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Traffic shift</div>
                        <div className="fw-bold">{finalGate.summary.traffic_shift_percent ?? 0}%</div>
                      </div>
                    </div>
                  </div>
                )}
                <div className="row g-2">
                  {(finalGate.criteria || []).map((item) => (
                    <div key={item.key} className="col-md-4">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="d-flex justify-content-between gap-2">
                          <div className="fw-semibold small">{item.label}</div>
                          <span className={`badge ${item.passed ? 'bg-success-subtle text-success' : 'bg-danger-subtle text-danger'}`}>
                            {item.passed ? 'Pass' : 'Review'}
                          </span>
                        </div>
                        {item.detail && <div className="small text-muted mt-2">{item.detail}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {TENANT_SECURITY_AUDIT_ENABLED && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
            <div>
              <h6 className="mb-0 fw-bold">Tenant Security Audit</h6>
              <div className="small text-muted">Ownership, phone routing, memory, CRM, and asset isolation readiness</div>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-outline-primary"
              onClick={fetchSecurityAuditReadiness}
              disabled={securityAuditLoading}
            >
              {securityAuditLoading ? 'Checking...' : 'Check now'}
            </button>
          </div>
          <div className="card-body">
            {!securityAudit ? (
              <p className="text-muted small mb-0">No tenant audit snapshot loaded yet.</p>
            ) : (
              <>
                <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                  <span className={`badge ${securityAudit.status === 'ready' ? 'bg-success' : 'bg-warning text-dark'}`}>
                    {securityAudit.status}
                  </span>
                  <span className="small text-muted">Mode: {securityAudit.mode || 'read_only'}</span>
                  {securityAudit.db_write_performed === false && (
                    <span className="badge bg-light text-dark border">No DB writes</span>
                  )}
                  {securityAudit.resource_payload_returned === false && (
                    <span className="badge bg-light text-dark border">No payloads returned</span>
                  )}
                  {securityAudit.audio_runtime_changed === false && (
                    <span className="badge bg-light text-dark border">Audio unchanged</span>
                  )}
                  {securityAudit.websocket_contract_changed === false && (
                    <span className="badge bg-light text-dark border">Websocket unchanged</span>
                  )}
                </div>
                {securityAudit.blockers?.length > 0 && (
                  <div className="alert alert-warning py-2 small mb-3">
                    Blockers: {securityAudit.blockers.join(', ')}
                  </div>
                )}
                {securityAudit.summary && (
                  <div className="row g-2 mb-3">
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Missing owners</div>
                        <div className="fw-bold">{securityAudit.summary.missing_owner_total ?? 0}</div>
                      </div>
                    </div>
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Scope mismatches</div>
                        <div className="fw-bold">{securityAudit.summary.relationship_mismatch_total ?? 0}</div>
                      </div>
                    </div>
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Phone mismatches</div>
                        <div className="fw-bold">{securityAudit.summary.phone_mismatch_total ?? 0}</div>
                      </div>
                    </div>
                    <div className="col-md-3">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="small text-muted">Tables checked</div>
                        <div className="fw-bold">{securityAudit.summary.tenant_tables_checked ?? 0}</div>
                      </div>
                    </div>
                  </div>
                )}
                <div className="row g-2">
                  {(securityAudit.criteria || []).map((item) => (
                    <div key={item.key} className="col-md-4">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="d-flex justify-content-between gap-2">
                          <div className="fw-semibold small">{item.label}</div>
                          <span className={`badge ${item.passed ? 'bg-success-subtle text-success' : 'bg-danger-subtle text-danger'}`}>
                            {item.passed ? 'Pass' : 'Review'}
                          </span>
                        </div>
                        {item.detail && <div className="small text-muted mt-2">{item.detail}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {DEMO_RUNTIME_QA_READINESS_ENABLED && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
            <div>
              <h6 className="mb-0 fw-bold">Demo Call QA</h6>
              <div className="small text-muted">Latency, intent, fallback, and recording readiness</div>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-outline-primary"
              onClick={fetchDemoQaReadiness}
              disabled={demoQaLoading}
            >
              {demoQaLoading ? 'Checking...' : 'Check now'}
            </button>
          </div>
          <div className="card-body">
            {!demoQa ? (
              <p className="text-muted small mb-0">No dry-run snapshot loaded yet.</p>
            ) : (
              <>
                <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                  <span className={`badge ${demoQa.status === 'ready' ? 'bg-success' : 'bg-warning text-dark'}`}>
                    {demoQa.status}
                  </span>
                  <span className="small text-muted">Mode: {demoQa.mode || 'read_only'}</span>
                  {demoQa.audio_contract_changed === false && (
                    <span className="badge bg-light text-dark border">Audio contract unchanged</span>
                  )}
                  {demoQa.websocket_contract_changed === false && (
                    <span className="badge bg-light text-dark border">Websocket contract unchanged</span>
                  )}
                </div>
                {demoQa.blockers?.length > 0 && (
                  <div className="alert alert-warning py-2 small mb-3">
                    Blockers: {demoQa.blockers.join(', ')}
                  </div>
                )}
                <div className="row g-2">
                  {(demoQa.criteria || []).map((item) => (
                    <div key={item.key} className="col-md-4">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="d-flex justify-content-between gap-2">
                          <div className="fw-semibold small">{item.label}</div>
                          <span className={`badge ${item.passed ? 'bg-success-subtle text-success' : 'bg-danger-subtle text-danger'}`}>
                            {item.passed ? 'Pass' : 'Review'}
                          </span>
                        </div>
                        {item.detail && <div className="small text-muted mt-2">{item.detail}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <div className="card border-0 shadow-sm mb-4">
        <div className="card-header bg-white border-bottom py-3">
          <h6 className="mb-0 fw-bold">Provider Latency</h6>
        </div>
        <div className="card-body">
          {Object.keys(providerMetrics).length === 0 ? (
            <p className="text-muted small mb-0">No STT/TTS samples captured yet.</p>
          ) : (
            <div className="row g-3">
              {Object.entries(providerMetrics).map(([key, metric]) => (
                <div key={key} className="col-md-3">
                  <div className="border rounded-3 p-3 h-100">
                    <div className="small text-muted text-uppercase fw-semibold mb-1">{formatProviderMetricKey(key)}</div>
                    <div className="fw-bold">{Math.round(metric.latest_ms || 0)} ms latest</div>
                    <div className="small text-muted">p50 {Math.round(metric.p50_ms || 0)} ms - p95 {Math.round(metric.p95_ms || 0)} ms</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card border-0 shadow-sm">
        <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
          <h6 className="mb-0 fw-bold">Active Outbound Legs</h6>
          <button className="btn btn-sm btn-outline-secondary">Filter</button>
        </div>
        <div className="table-responsive">
          <table className="table table-hover align-middle mb-0">
            <thead className="table-light text-muted small">
              <tr>
                <th className="fw-semibold px-4 py-3">Lead Name</th>
                <th className="fw-semibold py-3">Provider</th>
                <th className="fw-semibold py-3">Status</th>
                <th className="fw-semibold py-3">Latest Transcript</th>
                <th className="fw-semibold py-3 text-end px-4">Actions</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(liveCalls).length === 0 ? (
                <tr>
                  <td colSpan="5" className="text-center py-5 text-muted">
                    No active calls routing currently.
                  </td>
                </tr>
              ) : Object.values(liveCalls).map(call => (
                <tr key={call.lead_id}>
                  <td className="px-4 py-3 fw-medium">
                    <div className="d-flex align-items-center gap-2">
                      <div className="bg-light rounded-circle d-flex justify-content-center align-items-center text-secondary" style={{ width:'32px', height:'32px' }}>
                        👤
                      </div>
                      {call.lead_name || 'Unknown Lead'}
                    </div>
                  </td>
                  <td className="py-3">
                    <span className="badge bg-light text-dark shadow-sm border">{getProviderLabel('telephony', call.provider || 'twilio')}</span>
                  </td>
                  <td className="py-3">
                    {getStatusBadge(call.status)}
                  </td>
                  <td className="py-3 text-muted small" style={{ maxWidth: '300px' }}>
                    <div className="text-truncate fst-italic">
                      {call.last_snippet ? `"${call.last_snippet}"` : (call.status === 'completed' ? 'Call finished.' : 'Listening...')}
                    </div>
                  </td>
                  <td className="py-3 px-4 text-end">
                    <button className="btn btn-sm btn-light border shadow-sm">Inspect</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </DashboardLayout>
  );
}
