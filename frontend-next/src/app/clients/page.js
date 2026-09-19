'use client';
import { useState, useEffect } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth } from '@/context/AuthContext';

export default function ClientsPage() {
  const { user } = useAuth();
  const [showModal, setShowModal] = useState(false);
  const [clients, setClients] = useState([]);
  const [loading, setLoading] = useState(true);
  
  const [formData, setFormData] = useState({
    id: '',
    name: '',
    email: '',
    agent: '',
    agentId: ''
  });

  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  useEffect(() => {
    fetch(`${API}/api/clients`)
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        // Merge with clientProfile if needed, or just use DB data
        // For now, let's just use DB data but add initials for UI
        const dataArray = Array.isArray(data) ? data : [];
        const formatted = dataArray.map(c => ({
          ...c,
          initials: c.name ? c.name.substring(0, 2).toUpperCase() : '??',
          agent: c.agentName || 'None',
          agentId: c.agentId || 'none'
        }));
        setClients(formatted);
        setLoading(false);
      })
      .catch(e => {
        console.error("Failed to load clients", e);
        setLoading(false);
      });
  }, [API]);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!formData.id || !formData.name) return;
    
    try {
      const payload = {
        id: formData.id.toLowerCase().replace(/\s+/g, '-'),
        name: formData.name,
        email: formData.email.trim().toLowerCase(),
        agentName: formData.agent || 'Default',
        agentId: formData.agentId || 'default'
      };

      const res = await fetch(`${API}/api/clients`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        const saved = await res.json();
        setClients([...clients, {
          ...saved,
          initials: saved.name.substring(0, 2).toUpperCase(),
          agent: saved.agentName,
          agentId: saved.agentId
        }]);
        setShowModal(false);
        setFormData({ id: '', name: '', email: '', agent: '', agentId: '' });
      }
    } catch (err) {
      console.error("Failed to create client", err);
    }
  };

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Clients</h2>
          <p className="text-muted small mb-0">Manage customer accounts and access</p>
        </div>
        {user?.role === 'admin' && (
          <button className="btn btn-primary btn-sm px-3 shadow-sm" onClick={() => setShowModal(true)}>
            + Add Client
          </button>
        )}
      </div>
      
      {clients.length === 0 ? (
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <div className="fs-1 mb-3">🏢</div>
            <h6>Client Directory</h6>
            <p className="small">No clients added yet.</p>
          </div>
        </div>
      ) : (
        <div className="row g-4">
          {clients.map((client) => (
            <div key={client.id} className="col-md-4">
              <div className="card border-0 shadow-sm h-100">
                <div className="card-body">
                  <div className="d-flex align-items-center gap-3 mb-3">
                    <div className="bg-primary-subtle text-primary fw-bold rounded d-flex justify-content-center align-items-center" style={{ width: '48px', height: '48px', fontSize: '18px' }}>
                      {client.initials}
                    </div>
                    <div>
                      <h5 className="fw-bold mb-0">{client.name}</h5>
                      <span className="text-muted small">ID: {client.id}</span>
                      {client.email && <div className="text-muted small">{client.email}</div>}
                    </div>
                  </div>
                  <div className="d-flex justify-content-between text-muted small mb-2">
                    <span>Default Agent:</span>
                    <span className="fw-medium text-dark">{client.agent} ({client.agentId})</span>
                  </div>
                  <div className="d-flex justify-content-between text-muted small">
                    <span>Access Level:</span>
                    <span className="badge bg-secondary-subtle text-secondary border">Client</span>
                  </div>
                </div>
                <div className="card-footer bg-white border-top py-3 d-flex gap-2">
                  <button className="btn btn-sm btn-light border flex-grow-1">Manage</button>
                  <button className="btn btn-sm btn-light border text-danger">Revoke</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
          <div className="modal-dialog modal-dialog-centered">
            <div className="modal-content border-0 shadow">
              <div className="modal-header border-bottom-0 pb-0">
                <h5 className="modal-title fw-bold">Add New Client</h5>
                <button type="button" className="btn-close shadow-none" onClick={() => setShowModal(false)}></button>
              </div>
              <div className="modal-body">
                <form onSubmit={handleCreate}>
                  <div className="mb-3">
                    <label className="form-label small fw-bold">Company / Client Name</label>
                    <input type="text" className="form-control" required value={formData.name} onChange={e => setFormData({...formData, name: e.target.value, id: e.target.value.toLowerCase().replace(/\s+/g, '-')})} placeholder="e.g. Acme Corp" />
                  </div>
                  
                  <div className="mb-3">
                    <label className="form-label small fw-bold">Client ID (Login Slug)</label>
                    <input type="text" className="form-control" required value={formData.id} onChange={e => setFormData({...formData, id: e.target.value})} placeholder="e.g. acme-corp" />
                  </div>

                  <div className="mb-3">
                    <label className="form-label small fw-bold">User Email</label>
                    <input type="email" className="form-control" value={formData.email} onChange={e => setFormData({...formData, email: e.target.value})} placeholder="client@example.com" />
                  </div>

                  <div className="row g-3 mb-4">
                    <div className="col-md-6">
                      <label className="form-label small fw-bold">Default Agent Name</label>
                      <input type="text" className="form-control" value={formData.agent} onChange={e => setFormData({...formData, agent: e.target.value})} placeholder="e.g. Sarah" />
                    </div>
                    <div className="col-md-6">
                      <label className="form-label small fw-bold">Agent ID Map</label>
                      <input type="text" className="form-control" value={formData.agentId} onChange={e => setFormData({...formData, agentId: e.target.value})} placeholder="e.g. acme-agent-1" />
                    </div>
                  </div>

                  <div className="alert alert-info py-2 small">
                    Client records persist in the backend and can be matched by email during agent assignment.
                  </div>

                  <div className="d-flex justify-content-end gap-2">
                    <button type="button" className="btn btn-light border" onClick={() => setShowModal(false)}>Cancel</button>
                    <button type="submit" className="btn btn-primary px-4">Save Client</button>
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
