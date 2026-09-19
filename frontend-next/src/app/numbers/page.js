'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth } from '@/context/AuthContext';
import { getProviderLabel } from '@/lib/providerDisplay';

const API = process.env.NEXT_PUBLIC_API_URL || '';
const TELEPHONY_LIVE_QA_READINESS_ENABLED = process.env.NEXT_PUBLIC_TELEPHONY_LIVE_QA_READINESS_ENABLED === 'true';

function normalizeSearchResult(number, fallbackProvider) {
  const phone = number.phone || number.phone_number || '';
  return {
    ...number,
    phone,
    phone_number: phone,
    provider: number.provider || fallbackProvider,
    locality: number.locality || number.region || '',
    region: number.region || number.locality || '',
    monthly_cost: number.monthly_cost || number.cost || '',
    capabilities: number.capabilities || { voice: true },
  };
}

export default function MyNumbers() {
  const { activeClient, user } = useAuth();
  const [numbers, setNumbers] = useState([]);
  const [agents, setAgents] = useState([]);
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchProvider, setSearchProvider] = useState('twilio');
  const [searchCountry, setSearchCountry] = useState('IN');
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [busyKey, setBusyKey] = useState('');
  const [routeDrafts, setRouteDrafts] = useState({});
  const [liveQa, setLiveQa] = useState(null);
  const [liveQaLoading, setLiveQaLoading] = useState(false);
  const [notice, setNotice] = useState(null);

  const providerOptions = useMemo(() => {
    if (providers.length) return providers;
    return [
      { slug: 'twilio', display_name: getProviderLabel('telephony', 'twilio'), configured: false },
      { slug: 'vobiz', display_name: getProviderLabel('telephony', 'vobiz'), configured: false },
      { slug: 'exotel', display_name: getProviderLabel('telephony', 'exotel'), configured: false },
      { slug: 'knowlarity', display_name: getProviderLabel('telephony', 'knowlarity'), configured: false },
      { slug: 'demo', display_name: getProviderLabel('telephony', 'demo'), configured: true },
    ];
  }, [providers]);

  const loadNumbers = useCallback(async () => {
    if (user?.role !== 'admin') return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/telephony/numbers?client_id=${encodeURIComponent(activeClient || '')}`);
      const json = res.ok ? await res.json() : [];
      const rows = Array.isArray(json) ? json : [];
      setNumbers(rows);
      setRouteDrafts((current) => {
        const next = { ...current };
        rows.forEach((number) => {
          const savedAgentId = number.route_agent_id || number.route?.agent_id || '';
          if (next[number.id] === undefined) next[number.id] = savedAgentId;
        });
        return next;
      });
    } catch (e) {
      setNumbers([]);
    } finally {
      setLoading(false);
    }
  }, [activeClient, user?.role]);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (active) loadNumbers();
    });
    return () => {
      active = false;
    };
  }, [loadNumbers]);

  useEffect(() => {
    if (user?.role !== 'admin') return;
    let active = true;

    const loadProvidersAndAgents = async () => {
      try {
        const [providerRes, agentRes] = await Promise.all([
          fetch(`${API}/api/telephony/providers`),
          fetch(`${API}/api/agents?client_id=${encodeURIComponent(activeClient || '')}`),
        ]);
        const providerJson = providerRes.ok ? await providerRes.json() : [];
        const agentJson = agentRes.ok ? await agentRes.json() : [];
        if (!active) return;
        setProviders(Array.isArray(providerJson) ? providerJson : []);
        setAgents(Array.isArray(agentJson) ? agentJson : []);
      } catch (e) {
        if (!active) return;
        setProviders([]);
        setAgents([]);
      }
    };

    loadProvidersAndAgents();
    return () => {
      active = false;
    };
  }, [activeClient, user?.role]);

  const handleSearch = async () => {
    setSearching(true);
    setNotice(null);
    try {
      const res = await fetch(
        `${API}/api/telephony/numbers/search?provider=${encodeURIComponent(searchProvider)}&country_code=${encodeURIComponent(searchCountry)}`,
        { method: 'POST' },
      );
      const json = res.ok ? await res.json() : [];
      setSearchResults(Array.isArray(json) ? json.map((item) => normalizeSearchResult(item, searchProvider)) : []);
    } catch (e) {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  };

  const handleLiveQa = async (includeProviderProbe = false) => {
    if (!TELEPHONY_LIVE_QA_READINESS_ENABLED || user?.role !== 'admin') return;
    setLiveQaLoading(true);
    setNotice(null);
    try {
      const params = new URLSearchParams({
        provider: searchProvider,
        countryCode: searchCountry,
        includeProviderProbe: includeProviderProbe ? 'true' : 'false',
      });
      if (activeClient) params.set('clientId', activeClient);
      const res = await fetch(`${API}/api/telephony/live-qa/readiness?${params.toString()}`);
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        setLiveQa({
          status: 'disabled',
          blockers: [json.detail || `HTTP ${res.status}`],
          criteria: [],
          summary: {},
        });
        return;
      }
      setLiveQa(json);
    } catch (e) {
      setLiveQa({
        status: 'unavailable',
        blockers: ['request_failed'],
        criteria: [],
        summary: {},
      });
    } finally {
      setLiveQaLoading(false);
    }
  };

  const handleBuy = async (number) => {
    const phone = number.phone || number.phone_number;
    if (!phone || !activeClient) return;
    setBusyKey(`buy:${phone}`);
    setNotice(null);
    try {
      const res = await fetch(`${API}/api/telephony/numbers/purchase`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phoneNumber: phone,
          provider: number.provider || searchProvider,
          clientId: activeClient,
        }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(json.detail || 'Number purchase failed');
      await loadNumbers();
      setNotice({ type: 'success', text: `${phone} is now assigned to this client.` });
    } catch (e) {
      setNotice({ type: 'danger', text: e.message || 'Number purchase failed.' });
    } finally {
      setBusyKey('');
    }
  };

  const handleRouteSave = async (number) => {
    if (!number?.id || !activeClient) return;
    const selectedAgentId = routeDrafts[number.id] || null;
    setBusyKey(`route:${number.id}`);
    setNotice(null);
    try {
      const routeRes = await fetch(`${API}/api/telephony/numbers/routes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          numberId: number.id,
          clientId: activeClient,
          agentId: selectedAgentId,
          routingMode: 'tenant_default',
          metadata: { source: 'numbers_ui' },
        }),
      });

      if (!routeRes.ok) {
        const assignRes = await fetch(`${API}/api/telephony/numbers/assign`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ numberId: number.id, clientId: activeClient }),
        });
        const assignJson = await assignRes.json().catch(() => ({}));
        if (!assignRes.ok) throw new Error(assignJson.detail || 'Number assignment failed');
      }

      await loadNumbers();
      setNotice({ type: 'success', text: 'Number routing saved.' });
    } catch (e) {
      setNotice({ type: 'danger', text: e.message || 'Number routing failed.' });
    } finally {
      setBusyKey('');
    }
  };

  if (user?.role !== 'admin') {
    return (
      <DashboardLayout>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <h2 className="h5 fw-bold text-dark mb-2">Telephony access restricted</h2>
            <p className="small mb-0">Phone provisioning is available only to platform admins.</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">My Phone Numbers</h2>
          <p className="text-muted small mb-0">Numbers are scoped to the selected client.</p>
        </div>
      </div>

      {notice && (
        <div className={`alert alert-${notice.type} py-2 small`} role="status">
          {notice.text}
        </div>
      )}

      {TELEPHONY_LIVE_QA_READINESS_ENABLED && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center gap-3 flex-wrap">
            <div>
              <h6 className="mb-0 fw-bold">Telephony Live QA</h6>
              <div className="small text-muted">Provider setup, tenant routes, and webhook readiness</div>
            </div>
            <div className="d-flex gap-2">
              <button
                type="button"
                className="btn btn-sm btn-outline-primary"
                onClick={() => handleLiveQa(false)}
                disabled={liveQaLoading}
              >
                {liveQaLoading ? 'Checking...' : 'Check readiness'}
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => handleLiveQa(true)}
                disabled={liveQaLoading}
              >
                Live provider probe
              </button>
            </div>
          </div>
          <div className="card-body">
            {!liveQa ? (
              <p className="text-muted small mb-0">No telephony preflight loaded yet.</p>
            ) : (
              <>
                <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                  <span className={`badge ${liveQa.status === 'ready' ? 'bg-success' : 'bg-warning text-dark'}`}>
                    {liveQa.status}
                  </span>
                  <span className="small text-muted">Provider: {getProviderLabel('telephony', liveQa.provider || searchProvider)}</span>
                  {liveQa.outbound_calls_started === false && (
                    <span className="badge bg-light text-dark border">No calls started</span>
                  )}
                  {liveQa.numbers_purchased === false && (
                    <span className="badge bg-light text-dark border">No numbers purchased</span>
                  )}
                  {liveQa.tenant_routes_modified === false && (
                    <span className="badge bg-light text-dark border">Routes unchanged</span>
                  )}
                </div>
                {liveQa.blockers?.length > 0 && (
                  <div className="alert alert-warning py-2 small mb-3">
                    Blockers: {liveQa.blockers.join(', ')}
                  </div>
                )}
                <div className="row g-2">
                  {(liveQa.criteria || []).map((item) => (
                    <div key={item.key} className="col-md-4">
                      <div className="border rounded-3 p-3 h-100">
                        <div className="d-flex justify-content-between gap-2">
                          <div className="fw-semibold small">{item.label}</div>
                          <span className={`badge ${item.passed ? 'bg-success-subtle text-success' : 'bg-danger-subtle text-danger'}`}>
                            {item.passed ? 'Pass' : item.required ? 'Blocker' : 'Review'}
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
          <h6 className="mb-0 fw-bold">Search and Buy a Number</h6>
        </div>
        <div className="card-body p-4 d-flex align-items-end gap-3 flex-wrap">
          <div style={{ minWidth: '220px' }}>
            <label className="form-label small fw-semibold text-muted">Provider</label>
            <select className="form-select shadow-none" value={searchProvider} onChange={(e) => setSearchProvider(e.target.value)}>
              {providerOptions.map((provider) => (
                <option key={provider.slug} value={provider.slug}>
                  {provider.display_name || getProviderLabel('telephony', provider.slug)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="form-label small fw-semibold text-muted">Country</label>
            <select className="form-select shadow-none" value={searchCountry} onChange={(e) => setSearchCountry(e.target.value)}>
              <option value="IN">India</option>
              <option value="US">United States</option>
              <option value="GB">United Kingdom</option>
            </select>
          </div>
          <button className="btn btn-primary px-4 shadow-sm" onClick={handleSearch} disabled={searching}>
            {searching ? 'Searching...' : 'Search Numbers'}
          </button>
        </div>
      </div>

      {searchResults !== null && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3">
            <h6 className="mb-0 fw-bold">Available Numbers</h6>
          </div>
          <div className="card-body p-0">
            {searchResults.length === 0 ? (
              <div className="p-4 text-muted text-center small">No numbers found.</div>
            ) : (
              <div className="table-responsive">
                <table className="table table-hover align-middle mb-0">
                  <thead className="table-light text-muted small">
                    <tr>
                      <th className="px-4 py-3">Number</th>
                      <th className="py-3">Region</th>
                      <th className="py-3">Provider</th>
                      <th className="py-3">Cost</th>
                      <th className="py-3 text-end px-4">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {searchResults.map((number) => {
                      const phone = number.phone || number.phone_number;
                      return (
                        <tr key={`${number.provider}:${phone}`}>
                          <td className="px-4 py-3 fw-bold">{phone}</td>
                          <td className="py-3 text-muted">{number.locality || number.region || '-'}</td>
                          <td className="py-3">
                            <span className="badge bg-primary-subtle text-primary border border-primary-subtle rounded-pill">
                              {getProviderLabel('telephony', number.provider)}
                            </span>
                          </td>
                          <td className="py-3 text-muted">{number.monthly_cost || '-'}</td>
                          <td className="py-3 px-4 text-end">
                            <button
                              className="btn btn-sm btn-outline-primary"
                              onClick={() => handleBuy(number)}
                              disabled={busyKey === `buy:${phone}`}
                            >
                              {busyKey === `buy:${phone}` ? 'Buying...' : 'Buy'}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="card border-0 shadow-sm">
        <div className="card-header bg-white border-bottom py-3">
          <h6 className="mb-0 fw-bold">My Numbers</h6>
        </div>
        <div className="card-body p-0">
          {loading ? (
            <div className="p-4 text-center text-muted small">Loading...</div>
          ) : numbers.length === 0 ? (
            <div className="p-5 text-center text-muted small">No numbers yet. Search and buy one above.</div>
          ) : (
            <div className="table-responsive">
              <table className="table table-hover align-middle mb-0">
                <thead className="table-light text-muted small">
                  <tr>
                    <th className="px-4 py-3">Number</th>
                    <th className="py-3">Region</th>
                    <th className="py-3">Provider</th>
                    <th className="py-3">Agent Route</th>
                    <th className="py-3">Status</th>
                    <th className="py-3 text-end px-4">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {numbers.map((number) => (
                    <tr key={number.id || number.phone}>
                      <td className="px-4 py-3 fw-bold">{number.phone}</td>
                      <td className="py-3 text-muted">{number.region || '-'}</td>
                      <td className="py-3">
                        <span className="badge bg-primary-subtle text-primary border border-primary-subtle rounded-pill">
                          {getProviderLabel('telephony', number.provider)}
                        </span>
                      </td>
                      <td className="py-3" style={{ minWidth: '220px' }}>
                        <select
                          className="form-select form-select-sm shadow-none"
                          value={routeDrafts[number.id] || ''}
                          onChange={(e) => setRouteDrafts((current) => ({ ...current, [number.id]: e.target.value }))}
                        >
                          <option value="">Tenant default</option>
                          {agents.map((agent) => (
                            <option key={agent.id} value={agent.id}>
                              {agent.name || agent.id}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="py-3">
                        <span className="badge bg-success-subtle text-success border border-success-subtle rounded-pill">
                          Active
                        </span>
                      </td>
                      <td className="py-3 px-4 text-end">
                        <button
                          className="btn btn-sm btn-outline-primary"
                          onClick={() => handleRouteSave(number)}
                          disabled={busyKey === `route:${number.id}`}
                        >
                          {busyKey === `route:${number.id}` ? 'Saving...' : 'Save Route'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </DashboardLayout>
  );
}
