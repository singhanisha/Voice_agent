'use client';

import { useCallback, useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth } from '@/context/AuthContext';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function getAdminIdToken(firebaseUser, currentRole) {
  if (currentRole !== 'admin' || !firebaseUser || typeof firebaseUser.getIdToken !== 'function') return null;
  try {
    return await firebaseUser.getIdToken();
  } catch (error) {
    console.error('Demo requests auth token unavailable', error);
    return null;
  }
}

function adminAuthHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const STATUS_OPTIONS = ['New', 'Contacted', 'Demo Scheduled', 'Converted', 'Closed'];

export default function DemoRequestsAdmin() {
  const { currentRole, loading: authLoading, firebaseUser } = useAuth();
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  // Filtering & Search
  const [selectedStatusTab, setSelectedStatusTab] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');

  // Status updating indicator
  const [updatingId, setUpdatingId] = useState(null);

  const fetchRequests = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getAdminIdToken(firebaseUser, currentRole);
      const res = await fetch(`${API}/api/demo-requests`, {
        headers: adminAuthHeaders(token),
      });
      if (!res.ok) {
        throw new Error(`Failed to load requests (HTTP ${res.status})`);
      }
      const data = await res.json();
      setRequests(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error(err);
      setError(err.message || 'Failed to fetch demo requests.');
    } finally {
      setLoading(false);
    }
  }, [currentRole, firebaseUser]);

  useEffect(() => {
    if (!authLoading && currentRole === 'admin') {
      fetchRequests();
    }
  }, [authLoading, currentRole, fetchRequests]);

  const handleStatusChange = async (requestId, newStatus) => {
    setUpdatingId(requestId);
    try {
      const token = await getAdminIdToken(firebaseUser, currentRole);
      const res = await fetch(`${API}/api/demo-requests/${requestId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...adminAuthHeaders(token),
        },
        body: JSON.stringify({ status: newStatus }),
      });

      if (!res.ok) {
        throw new Error('Failed to update status.');
      }

      // Update local state
      setRequests((prev) =>
        prev.map((req) => (req.id === requestId ? { ...req, status: newStatus } : req))
      );
    } catch (err) {
      alert(err.message || 'Error updating status.');
    } finally {
      setUpdatingId(null);
    }
  };

  // Filter requests based on selected tab and search query
  const filteredRequests = useMemo(() => {
    return requests.filter((req) => {
      const matchesTab = selectedStatusTab === 'All' || req.status === selectedStatusTab;
      
      const query = searchQuery.toLowerCase().trim();
      const matchesSearch = !query || 
        (req.name || '').toLowerCase().includes(query) ||
        (req.email || '').toLowerCase().includes(query) ||
        (req.company || '').toLowerCase().includes(query) ||
        (req.industry || '').toLowerCase().includes(query) ||
        (req.use_case || '').toLowerCase().includes(query);

      return matchesTab && matchesSearch;
    });
  }, [requests, selectedStatusTab, searchQuery]);

  const getStatusBadgeClass = (status) => {
    switch (status) {
      case 'New': return 'bg-danger-subtle text-danger border border-danger-subtle';
      case 'Contacted': return 'bg-primary-subtle text-primary border border-primary-subtle';
      case 'Demo Scheduled': return 'bg-warning-subtle text-warning-emphasis border border-warning-subtle';
      case 'Converted': return 'bg-success-subtle text-success border border-success-subtle';
      case 'Closed': return 'bg-secondary-subtle text-secondary border border-secondary-subtle';
      default: return 'bg-light text-dark border';
    }
  };

  const formatDate = (dateStr) => {
    try {
      const date = new Date(dateStr);
      return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) + ' ' +
             date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return dateStr;
    }
  };

  // Access guard
  if (!authLoading && currentRole !== 'admin') {
    return (
      <DashboardLayout>
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <h5 className="fw-bold mb-2">Admin Access Required</h5>
            <p className="small mb-0">Custom Demo Requests dashboard is available only to platform administrators.</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Custom Demo Requests</h2>
          <p className="text-muted small mb-0">Follow up and update the lifecycle of custom voice agent requests</p>
        </div>
        <button className="btn btn-outline-primary btn-sm px-3" onClick={fetchRequests} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh List'}
        </button>
      </div>

      {error && (
        <div className="alert alert-danger py-2 small" role="alert">
          {error}
        </div>
      )}

      {/* FILTER TABS & SEARCH BAR */}
      <div className="row g-3 mb-4 align-items-center">
        <div className="col-md-8">
          <div className="btn-group shadow-sm" role="group" aria-label="Status filter">
            {['All', ...STATUS_OPTIONS].map((tab) => (
              <button
                key={tab}
                type="button"
                className={`btn btn-sm ${selectedStatusTab === tab ? 'btn-primary' : 'btn-light border'}`}
                onClick={() => setSelectedStatusTab(tab)}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>
        <div className="col-md-4">
          <div className="input-group input-group-sm">
            <span className="input-group-text bg-white border-end-0">🔍</span>
            <input
              type="text"
              className="form-control border-start-0 shadow-none"
              placeholder="Search by name, company, email..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* REQUESTS LIST CARD */}
      <div className="card border-0 shadow-sm">
        <div className="table-responsive">
          <table className="table table-hover align-middle mb-0" style={{ fontSize: '13px' }}>
            <thead className="table-light text-muted small">
              <tr>
                <th className="fw-semibold px-4 py-3">Submitted</th>
                <th className="fw-semibold py-3">Submitter / Company</th>
                <th className="fw-semibold py-3">Industry / Use Case</th>
                <th className="fw-semibold py-3">Vol / Notes</th>
                <th className="fw-semibold py-3">Status</th>
                <th className="fw-semibold py-3 text-end pe-4">Lifecycle Action</th>
              </tr>
            </thead>
            <tbody>
              {loading && requests.length === 0 ? (
                <tr>
                  <td colSpan="6" className="text-center py-5 text-muted">
                    <span className="spinner-border spinner-border-sm me-2" role="status" />
                    Loading custom agent requests...
                  </td>
                </tr>
              ) : filteredRequests.length === 0 ? (
                <tr>
                  <td colSpan="6" className="text-center py-5 text-muted fst-italic">
                    No matching custom requests found.
                  </td>
                </tr>
              ) : filteredRequests.map((req) => (
                <tr key={req.id}>
                  {/* Submitted */}
                  <td className="px-4 py-3 text-secondary" style={{ width: '130px' }}>
                    {formatDate(req.created_at || req.id)}
                  </td>
                  
                  {/* Submitter Info */}
                  <td className="py-3">
                    <div className="fw-bold text-dark">{req.name}</div>
                    <div className="text-muted small">{req.email}</div>
                    {req.phone && <div className="text-muted small" style={{ fontSize: '11px' }}>📞 {req.phone}</div>}
                    <div className="mt-1"><span className="badge bg-light text-secondary border">{req.company || 'Individual'}</span></div>
                  </td>

                  {/* Industry & Use Case */}
                  <td className="py-3">
                    <div className="fw-bold">{req.use_case || 'General Outbound Agent'}</div>
                    <div className="text-muted small">Industry: {req.industry || 'Not specified'}</div>
                  </td>

                  {/* Volume / Notes */}
                  <td className="py-3" style={{ maxWidth: '280px' }}>
                    <div className="text-dark small"><span className="fw-semibold">Est. Vol:</span> {req.monthly_volume || 'N/A'}</div>
                    {req.additional_notes ? (
                      <div className="text-muted small text-truncate fst-italic mt-1" title={req.additional_notes}>
                        &ldquo;{req.additional_notes}&rdquo;
                      </div>
                    ) : (
                      <span className="text-muted small fst-italic mt-1">No notes</span>
                    )}
                  </td>

                  {/* Status Badge */}
                  <td className="py-3">
                    <span className={`badge px-2 py-1 ${getStatusBadgeClass(req.status)}`}>
                      {req.status}
                    </span>
                  </td>

                  {/* Actions Dropdown */}
                  <td className="py-3 text-end pe-4" style={{ width: '160px' }}>
                    <select
                      className="form-select form-select-sm shadow-none d-inline-block w-auto"
                      value={req.status || 'New'}
                      onChange={(e) => handleStatusChange(req.id, e.target.value)}
                      disabled={updatingId === req.id}
                      style={{ fontSize: '12px' }}
                    >
                      {STATUS_OPTIONS.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                    {updatingId === req.id && (
                      <span className="spinner-border spinner-border-sm ms-2 text-primary" role="status" />
                    )}
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

// React useMemo to memoize filtration logic safely
import { useMemo } from 'react';
