'use client';
import { useMemo, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth } from '@/context/AuthContext';

const CRM_READINESS_UI_ENABLED = process.env.NEXT_PUBLIC_CRM_READINESS_UI_ENABLED === 'true';

function valueText(value) {
  if (value === true) return 'Yes';
  if (value === false) return 'No';
  if (value === null || value === undefined || value === '') return 'None';
  return String(value);
}

function gateStatus(gate, payload) {
  if (!gate) return { label: 'Waiting', className: 'bg-secondary' };
  if (gate.error) {
    return {
      label: gate.status === 403 ? 'Disabled' : 'Blocked',
      className: gate.status === 403 ? 'bg-warning text-dark' : 'bg-danger',
    };
  }
  const readiness = payload?.readiness || {};
  const execution = payload?.execution || {};
  if (execution.canary_manifest_ready || execution.sandbox_ready || payload?.future_live_prerequisites_met) {
    return { label: 'Ready', className: 'bg-success' };
  }
  if ((readiness.blockers || []).length > 0) return { label: 'Blocked', className: 'bg-danger' };
  return { label: 'Checked', className: 'bg-primary' };
}

function GatePanel({ title, gate, payloadKey }) {
  const payload = gate?.data?.[payloadKey];
  const status = gateStatus(gate, payload);
  const blockers = payload?.readiness?.blockers || [];
  const safety = payload?.safety || {};
  const approval = payload?.approval || {};
  const execution = payload?.execution || {};

  return (
    <section className="card border-0 shadow-sm h-100">
      <div className="card-header bg-white border-bottom d-flex justify-content-between align-items-center py-3">
        <h6 className="mb-0 fw-bold">{title}</h6>
        <span className={`badge ${status.className}`}>{status.label}</span>
      </div>
      <div className="card-body">
        {gate?.error ? (
          <div className="text-muted small">{gate.error}</div>
        ) : payload ? (
          <>
            <div className="row g-2 small mb-3">
              <div className="col-6 text-muted">Provider</div>
              <div className="col-6 text-end fw-semibold">{valueText(payload.provider)}</div>
              <div className="col-6 text-muted">Object</div>
              <div className="col-6 text-end fw-semibold">{valueText(payload.object_type)}</div>
              <div className="col-6 text-muted">Approval</div>
              <div className="col-6 text-end fw-semibold">{valueText(approval.approval_status)}</div>
              <div className="col-6 text-muted">Records</div>
              <div className="col-6 text-end fw-semibold">
                {valueText(payload.record_count ?? payload.canary?.available_record_count)}
              </div>
            </div>

            <div className="d-flex flex-wrap gap-2 mb-3">
              <span className="badge bg-light text-dark border">Network: {valueText(safety.network_call_performed)}</span>
              <span className="badge bg-light text-dark border">Provider sent: {valueText(safety.sent_to_provider)}</span>
              <span className="badge bg-light text-dark border">Payload shown: {valueText(safety.provider_payload_included)}</span>
              <span className="badge bg-light text-dark border">Body shown: {valueText(safety.request_body_included)}</span>
              <span className="badge bg-light text-dark border">Credentials shown: {valueText(safety.credential_value_included)}</span>
              <span className="badge bg-light text-dark border">Dispatch: {valueText(execution.dispatch_allowed)}</span>
            </div>

            {blockers.length > 0 && (
              <div className="small">
                <div className="text-muted fw-semibold mb-2">Blockers</div>
                <div className="d-flex flex-wrap gap-2">
                  {blockers.map((blocker) => (
                    <span key={blocker} className="badge bg-warning-subtle text-warning border border-warning-subtle">
                      {blocker}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="text-muted small">No check loaded.</div>
        )}
      </div>
    </section>
  );
}

export default function CRMReadinessPage() {
  const { activeClient, user } = useAuth();
  const [clientId, setClientId] = useState(activeClient || '');
  const [outboxId, setOutboxId] = useState('');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState({});
  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  const headers = useMemo(() => ({
    'X-Tenant-Id': clientId,
    'X-User-Email': user?.email || '',
  }), [clientId, user?.email]);

  const fetchGate = async (key, path) => {
    const res = await fetch(`${API}${path}`, { method: 'GET', headers });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { key, status: res.status, error: body.detail || 'Request failed' };
    }
    return { key, status: res.status, data: body };
  };

  const loadReadiness = async (event) => {
    event.preventDefault();
    const cleanOutboxId = outboxId.trim();
    const cleanClientId = clientId.trim();
    if (!cleanOutboxId || !cleanClientId) return;

    setLoading(true);
    setResults({});
    const query = `clientId=${encodeURIComponent(cleanClientId)}`;
    const gates = await Promise.all([
      fetchGate('live', `/api/crm/outbox/${encodeURIComponent(cleanOutboxId)}/live-readiness?${query}`),
      fetchGate('sandbox', `/api/crm/outbox/${encodeURIComponent(cleanOutboxId)}/provider-sandbox?${query}`),
      fetchGate('canary', `/api/crm/outbox/${encodeURIComponent(cleanOutboxId)}/dispatch-canary?${query}`),
    ]);
    setResults(Object.fromEntries(gates.map((gate) => [gate.key, gate])));
    setLoading(false);
  };

  if (user?.role !== 'admin') {
    return (
      <DashboardLayout>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <h2 className="h5 fw-bold text-dark mb-2">CRM readiness access restricted</h2>
            <p className="small mb-0">CRM rollout checks are available only to platform admins.</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  if (!CRM_READINESS_UI_ENABLED) {
    return (
      <DashboardLayout>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <h2 className="h5 fw-bold text-dark mb-2">CRM readiness UI disabled</h2>
            <p className="small mb-0">Enable NEXT_PUBLIC_CRM_READINESS_UI_ENABLED to view CRM rollout checks.</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">CRM Readiness</h2>
          <p className="text-muted small mb-0">Read-only rollout gates for approved CRM outbox items</p>
        </div>
        <span className="badge bg-light text-dark border">No external dispatch</span>
      </div>

      <form className="card border-0 shadow-sm mb-4" onSubmit={loadReadiness}>
        <div className="card-body p-4">
          <div className="row g-3 align-items-end">
            <div className="col-md-4">
              <label className="form-label small fw-semibold text-muted">Client ID</label>
              <input
                className="form-control shadow-none"
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                placeholder="client-1"
              />
            </div>
            <div className="col-md-6">
              <label className="form-label small fw-semibold text-muted">Outbox ID</label>
              <input
                className="form-control shadow-none"
                value={outboxId}
                onChange={(event) => setOutboxId(event.target.value)}
                placeholder="crm outbox id"
              />
            </div>
            <div className="col-md-2 d-grid">
              <button className="btn btn-primary" type="submit" disabled={loading || !clientId.trim() || !outboxId.trim()}>
                {loading ? 'Checking...' : 'Check Gates'}
              </button>
            </div>
          </div>
        </div>
      </form>

      <div className="row g-4">
        <div className="col-lg-4">
          <GatePanel title="Live Readiness" gate={results.live} payloadKey="readiness" />
        </div>
        <div className="col-lg-4">
          <GatePanel title="Provider Sandbox" gate={results.sandbox} payloadKey="provider_sandbox" />
        </div>
        <div className="col-lg-4">
          <GatePanel title="Dispatch Canary" gate={results.canary} payloadKey="dispatch_canary" />
        </div>
      </div>
    </DashboardLayout>
  );
}
