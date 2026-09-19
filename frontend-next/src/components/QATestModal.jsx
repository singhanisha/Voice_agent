import React, { useState } from 'react';

export default function QATestModal({ agent, onClose, API, headers, onCertifySuccess }) {
  const [qaReport, setQaReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [stressLoading, setStressLoading] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const runQaTest = async () => {
    setLoading(true);
    setError('');
    setMessage('');
    setQaReport(null);
    try {
      const res = await fetch(`${API}/api/agents/${agent.id}/qa-test`, {
        method: 'POST',
        headers
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'QA Test Failed');
      }
      const data = await res.json();
      setQaReport(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const runStressTest = async () => {
    setStressLoading(true);
    setError('');
    setMessage('');
    try {
      const res = await fetch(`${API}/api/agents/${agent.id}/stress-test`, {
        method: 'POST',
        headers
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Stress Test Failed');
      }
      const data = await res.json();
      setMessage(data.message || 'Stress test queued successfully.');
    } catch (err) {
      setError(err.message);
    } finally {
      setStressLoading(false);
    }
  };

  const certifyAgent = async () => {
    setError('');
    setMessage('');
    try {
      const res = await fetch(`${API}/api/agents/${agent.id}/certify`, {
        method: 'PUT',
        headers
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Certification Failed');
      }
      setMessage('Agent certified successfully for production.');
      if (onCertifySuccess) onCertifySuccess();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
      <div className="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
        <div className="modal-content border-0 shadow">
          <div className="modal-header">
            <h5 className="modal-title fw-bold">Internal QA Simulator &mdash; {agent.name}</h5>
            <button type="button" className="btn-close" onClick={onClose}></button>
          </div>
          <div className="modal-body">
            {error && <div className="alert alert-danger">{error}</div>}
            {message && <div className="alert alert-success">{message}</div>}
            
            <div className="d-flex gap-3 mb-4">
              <button className="btn btn-primary" onClick={runQaTest} disabled={loading || stressLoading}>
                {loading ? 'Running QA...' : 'Run QA Test'}
              </button>
              <button className="btn btn-outline-warning" onClick={runStressTest} disabled={loading || stressLoading}>
                {stressLoading ? 'Queuing...' : 'Run Stress Test'}
              </button>
            </div>

            {loading && (
              <div className="text-center py-5">
                <span className="spinner-border text-primary"></span>
                <p className="mt-2 text-muted">Simulating conversations...</p>
              </div>
            )}

            {qaReport && !loading && (
              <div className="card shadow-sm border-0">
                <div className="card-header bg-light">
                  <h6 className="mb-0 fw-bold">QA Report Scorecard</h6>
                </div>
                <div className="card-body">
                  <h2 className={`mb-4 fw-bold ${qaReport.overall_score >= 95 ? 'text-success' : 'text-danger'}`}>
                    Overall Score: {qaReport.overall_score}/100
                  </h2>
                  <div className="row g-3 mb-4">
                    {Object.entries(qaReport.metrics || {}).map(([key, val]) => (
                      <div className="col-md-6" key={key}>
                        <div className="d-flex justify-content-between align-items-center border-bottom pb-2">
                          <span>{key}</span>
                          <span className={`badge ${val === 'PASS' ? 'bg-success' : 'bg-danger'}`}>{val}</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {qaReport.failures && qaReport.failures.length > 0 && (
                    <div className="mt-4">
                      <h6 className="fw-bold text-danger">Failure Report</h6>
                      {qaReport.failures.map((fail, i) => (
                        <div className="alert alert-warning mb-3" key={i}>
                          <strong>Test:</strong> {fail.test} <br/>
                          <strong>User Input:</strong> "{fail.user_input}" <br/>
                          <strong>Agent Response:</strong> "{fail.agent_response || 'N/A'}" <br/>
                          <strong>Expected:</strong> {fail.expected_response || 'N/A'} <br/>
                          <hr/>
                          <strong>Root Cause:</strong> {fail.root_cause} <br/>
                          <strong>Suggested Fix:</strong> {fail.suggested_fix}
                        </div>
                      ))}
                    </div>
                  )}
                  
                  {qaReport.failures && qaReport.failures.length === 0 && (
                    <div className="alert alert-success mt-4">
                      All tests passed! The agent is ready for production.
                    </div>
                  )}

                </div>
                <div className="card-footer bg-white border-top text-end">
                  <button 
                    className="btn btn-success" 
                    onClick={certifyAgent} 
                    disabled={qaReport.overall_score < 95}
                  >
                    Certify for Production
                  </button>
                </div>
              </div>
            )}
            
            {(!qaReport && !loading && (agent.last_qa_report || agent.qa_score !== null)) && (
               <div className="alert alert-info mt-3">
                 <strong>Previous QA Score:</strong> {agent.qa_score}/100 <br/>
                 <strong>Status:</strong> {agent.certification_status || 'Draft'}
               </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
