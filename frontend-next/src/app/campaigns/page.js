'use client';
import { useState, useEffect } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth } from '@/context/AuthContext';
import { getProviderLabel } from '@/lib/providerDisplay';

const CAMPAIGN_LIFECYCLE_ENABLED = process.env.NEXT_PUBLIC_CAMPAIGN_LIFECYCLE_ENABLED === 'true';
const CAMPAIGN_E2E_QA_READINESS_ENABLED = process.env.NEXT_PUBLIC_CAMPAIGN_E2E_QA_READINESS_ENABLED === 'true';

export default function CampaignsPage() {
  const { user, activeClient } = useAuth();
  const [campaigns, setCampaigns] = useState([]);
  const [agents, setAgents] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [campaignQa, setCampaignQa] = useState(null);
  const [campaignQaLoading, setCampaignQaLoading] = useState(false);
  
  const [formData, setFormData] = useState({
    campaignId: '',
    agentId: '',
    telephonyProvider: 'demo',
    leadsCsv: 'John Doe,+1234567890\nJane Smith,+0987654321'
  });

  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  const fetchData = async () => {
    setLoading(true);
    try {
      const campaignParams = CAMPAIGN_LIFECYCLE_ENABLED
        ? `?includeArchived=${includeArchived}&includeDeleted=${includeDeleted}`
        : '';
      const [cRes, aRes] = await Promise.all([
        fetch(`${API}/api/campaigns${campaignParams}`),
        fetch(`${API}/api/agents`)
      ]);
      if (cRes.ok) {
        const cData = await cRes.json();
        setCampaigns(Array.isArray(cData) ? cData : []);
      }
      if (aRes.ok) {
        const aData = await aRes.json();
        setAgents(Array.isArray(aData) ? aData : []);
      }
    } catch (err) {
      console.error('Failed to fetch data', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeArchived, includeDeleted]);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!formData.campaignId || !formData.agentId) return alert("Campaign ID and Agent required");

    // Parse leads
    const leads = formData.leadsCsv.split('\n').map(line => {
      const [name, phone] = line.split(',');
      if (name && phone) return { name: name.trim(), phone: phone.trim() };
      return null;
    }).filter(Boolean);

    try {
      // 1. Upload Leads
      await fetch(`${API}/api/leads/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaignId: formData.campaignId,
          campaignName: formData.campaignId,
          agentId: formData.agentId,
          telephonyProvider: formData.telephonyProvider,
          clientId: activeClient,
          leads
        })
      });

      // 2. Start Campaign
      const res = await fetch(`${API}/api/campaigns/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaignId: formData.campaignId,
          agentId: formData.agentId,
          telephonyProvider: formData.telephonyProvider,
          clientId: activeClient
        })
      });

      if (res.ok) {
        setShowModal(false);
        setFormData({ ...formData, campaignId: '' });
        fetchData();
      } else {
        alert("Failed to start campaign");
      }
    } catch (err) {
      console.error(err);
      alert("Error starting campaign");
    }
  };

  const handleCampaignQa = async () => {
    if (!CAMPAIGN_E2E_QA_READINESS_ENABLED || user?.role !== 'admin') return;
    setCampaignQaLoading(true);
    try {
      const params = new URLSearchParams();
      if (activeClient) params.set('clientId', activeClient);
      const query = params.toString() ? `?${params.toString()}` : '';
      const res = await fetch(`${API}/api/campaigns/e2e-qa/readiness${query}`);
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        setCampaignQa({
          status: 'disabled',
          blockers: [json.detail || `HTTP ${res.status}`],
          criteria: [],
          summary: {},
        });
        return;
      }
      setCampaignQa(json);
    } catch (err) {
      console.error('Campaign QA readiness failed', err);
      setCampaignQa({
        status: 'unavailable',
        blockers: ['request_failed'],
        criteria: [],
        summary: {},
      });
    } finally {
      setCampaignQaLoading(false);
    }
  };

  const handleLifecycleAction = async (campaign, action) => {
    const labels = {
      archive: 'archive',
      restore: 'restore',
      delete: 'soft delete'
    };
    if (!window.confirm(`Are you sure you want to ${labels[action]} this campaign?`)) return;
    try {
      const method = action === 'delete' ? 'DELETE' : 'POST';
      const suffix = action === 'delete' ? '' : `/${action}`;
      const res = await fetch(`${API}/api/campaigns/${campaign.id}${suffix}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reason: action === 'delete' ? 'Archived from campaign dashboard' : undefined,
          actorEmail: user?.email || undefined
        })
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Campaign update failed');
      }
      await fetchData();
    } catch (err) {
      alert(err.message || 'Campaign update failed');
    }
  };

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Campaigns</h2>
          <p className="text-muted small mb-0">Manage outbound voice campaigns</p>
        </div>
        {user?.role === 'admin' && (
          <button className="btn btn-primary btn-sm px-3 shadow-sm" onClick={() => setShowModal(true)}>
            + New Campaign
          </button>
        )}
      </div>

      {CAMPAIGN_LIFECYCLE_ENABLED && (
        <div className="d-flex justify-content-end gap-3 mb-3 small">
          <label className="d-flex align-items-center gap-2 text-muted">
            <input type="checkbox" checked={includeArchived} onChange={e => setIncludeArchived(e.target.checked)} />
            Show archived
          </label>
          <label className="d-flex align-items-center gap-2 text-muted">
            <input type="checkbox" checked={includeDeleted} onChange={e => setIncludeDeleted(e.target.checked)} />
            Show deleted
          </label>
        </div>
      )}

      {CAMPAIGN_E2E_QA_READINESS_ENABLED && user?.role === 'admin' && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center gap-3 flex-wrap">
            <div>
              <h6 className="mb-0 fw-bold">Campaign E2E QA</h6>
              <div className="small text-muted">Launch, live updates, results, transcripts, and recordings readiness</div>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-outline-primary"
              onClick={handleCampaignQa}
              disabled={campaignQaLoading}
            >
              {campaignQaLoading ? 'Checking...' : 'Check readiness'}
            </button>
          </div>
          <div className="card-body">
            {!campaignQa ? (
              <p className="text-muted small mb-0">No campaign QA snapshot loaded yet.</p>
            ) : (
              <>
                <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                  <span className={`badge ${campaignQa.status === 'ready' ? 'bg-success' : 'bg-warning text-dark'}`}>
                    {campaignQa.status}
                  </span>
                  <span className="small text-muted">Mode: {campaignQa.mode || 'read_only'}</span>
                  {campaignQa.campaigns_started === false && (
                    <span className="badge bg-light text-dark border">No campaigns started</span>
                  )}
                  {campaignQa.outbound_calls_started === false && (
                    <span className="badge bg-light text-dark border">No calls started</span>
                  )}
                  {campaignQa.results_written === false && (
                    <span className="badge bg-light text-dark border">No results written</span>
                  )}
                  {campaignQa.queue_dispatch_started === false && (
                    <span className="badge bg-light text-dark border">Queue unchanged</span>
                  )}
                </div>
                {campaignQa.blockers?.length > 0 && (
                  <div className="alert alert-warning py-2 small mb-3">
                    Blockers: {campaignQa.blockers.join(', ')}
                  </div>
                )}
                <div className="row g-2">
                  {(campaignQa.criteria || []).map((item) => (
                    <div key={item.key} className="col-md-4">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="d-flex justify-content-between gap-2">
                          <div className="fw-semibold small">{item.label}</div>
                          <span className={`badge ${item.passed ? 'bg-success-subtle text-success' : 'bg-danger-subtle text-danger'}`}>
                            {item.passed ? 'Pass' : 'Blocker'}
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
      
      {loading ? (
        <div className="text-center py-5"><span className="spinner-border text-primary"></span></div>
      ) : campaigns.length === 0 ? (
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <div className="fs-1 mb-3">🚀</div>
            <h6>No active campaigns</h6>
            <p className="small">Click &apos;New Campaign&apos; to start dialing.</p>
          </div>
        </div>
      ) : (
        <div className="card border-0 shadow-sm">
          <div className="table-responsive">
            <table className="table table-hover align-middle mb-0">
              <thead className="table-light text-muted small">
                <tr>
                  <th className="py-3 px-4">Campaign</th>
                  <th className="py-3">Status</th>
                  <th className="py-3">Provider</th>
                  <th className="py-3">Agent</th>
                  <th className="py-3 text-end">Leads</th>
                  <th className="py-3 text-end px-4">Created</th>
                  {CAMPAIGN_LIFECYCLE_ENABLED && <th className="py-3 text-end px-4">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.id}>
                    <td className="px-4 py-3">
                      <div className="fw-medium">{c.name || c.id}</div>
                      {c.name && <div className="text-muted small">{c.id}</div>}
                    </td>
                    <td className="py-3">
                      <span className={`badge ${c.status === 'Active' ? 'bg-success' : 'bg-secondary'}`}>{c.status || 'Pending'}</span>
                      {c.archived_at && <span className="badge bg-warning-subtle text-warning border ms-2">Archived</span>}
                      {c.deleted_at && <span className="badge bg-danger-subtle text-danger border ms-2">Deleted</span>}
                    </td>
                    <td className="py-3"><span className="badge bg-light text-dark border">{getProviderLabel('telephony', c.telephony_provider || 'demo')}</span></td>
                    <td className="py-3 text-muted">{c.agent_id}</td>
                    <td className="py-3 text-end fw-semibold">{c.lead_count ?? 0}</td>
                    <td className="py-3 px-4 text-end text-muted small">{new Date(c.created_at).toLocaleString()}</td>
                    {CAMPAIGN_LIFECYCLE_ENABLED && (
                      <td className="py-3 px-4 text-end">
                        <div className="d-flex justify-content-end gap-2">
                          {c.archived_at || c.deleted_at ? (
                            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => handleLifecycleAction(c, 'restore')}>
                              Restore
                            </button>
                          ) : (
                            <>
                              <button type="button" className="btn btn-outline-secondary btn-sm" disabled={c.status === 'Active'} onClick={() => handleLifecycleAction(c, 'archive')}>
                                Archive
                              </button>
                              <button type="button" className="btn btn-outline-danger btn-sm" disabled={c.status === 'Active'} onClick={() => handleLifecycleAction(c, 'delete')}>
                                Delete
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
          <div className="modal-dialog modal-dialog-centered">
            <div className="modal-content border-0 shadow">
              <div className="modal-header border-bottom-0 pb-0">
                <h5 className="modal-title fw-bold">Start New Campaign</h5>
                <button type="button" className="btn-close shadow-none" onClick={() => setShowModal(false)}></button>
              </div>
              <div className="modal-body">
                <form onSubmit={handleCreate}>
                  <div className="mb-3">
                    <label className="form-label small fw-bold">Campaign ID (No spaces)</label>
                    <input type="text" className="form-control" required value={formData.campaignId} onChange={e => setFormData({...formData, campaignId: e.target.value.replace(/\s+/g, '-')})} placeholder="e.g. spring-sale-2024" />
                  </div>
                  
                  <div className="row g-3 mb-3">
                    <div className="col-md-6">
                      <label className="form-label small fw-bold">Select Agent</label>
                      <select className="form-select" required value={formData.agentId} onChange={e => setFormData({...formData, agentId: e.target.value})}>
                        <option value="">-- Select Agent --</option>
                        {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                        <option value="default">Default Agent</option>
                      </select>
                    </div>
                    <div className="col-md-6">
                      <label className="form-label small fw-bold">Telephony Provider</label>
                      <select className="form-select" value={formData.telephonyProvider} onChange={e => setFormData({...formData, telephonyProvider: e.target.value})}>
                        <option value="twilio">{getProviderLabel('telephony', 'twilio')}</option>
                        <option value="demo">{getProviderLabel('telephony', 'demo')}</option>
                      </select>
                    </div>
                  </div>

                  <div className="mb-4">
                    <label className="form-label small fw-bold">Leads (Name, Phone Number) - 1 per line</label>
                    <textarea className="form-control" rows="4" required value={formData.leadsCsv} onChange={e => setFormData({...formData, leadsCsv: e.target.value})} placeholder="John Doe,+1234567890"></textarea>
                    <div className="form-text small">Ensure phone numbers include country code.</div>
                  </div>

                  <div className="d-flex justify-content-end gap-2">
                    <button type="button" className="btn btn-light border" onClick={() => setShowModal(false)}>Cancel</button>
                    <button type="submit" className="btn btn-primary px-4">Launch Campaign</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}
