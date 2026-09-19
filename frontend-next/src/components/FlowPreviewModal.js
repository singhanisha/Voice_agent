'use client';

import { useState } from 'react';

const emptyList = [];

function BadgeList({ values }) {
  const items = values?.length ? values : emptyList;
  if (!items.length) return <span className="text-muted">None</span>;
  return (
    <div className="d-flex flex-wrap gap-1">
      {items.map((value, index) => (
        <span key={`${value}-${index}`} className="badge bg-light text-dark border">
          {value}
        </span>
      ))}
    </div>
  );
}

function PathList({ title, items }) {
  return (
    <div className="border rounded p-3 h-100 bg-white">
      <div className="fw-bold small text-uppercase text-muted mb-2">{title}</div>
      {items?.length ? (
        <div className="d-flex flex-column gap-2">
          {items.map((item, index) => (
            <div key={`${item.from_node_id}-${item.intent}-${index}`} className="small">
              <div className="fw-semibold text-dark">{item.from_label}</div>
              <div className="text-muted">
                {String(item.intent || '').replaceAll('_', ' ')} &rarr; {item.target_label}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-muted small">No paths</div>
      )}
    </div>
  );
}

function AuditChecklist({ audit }) {
  const website = audit?.website_intelligence;
  if (!website) return null;
  const checklist = website.review_checklist || emptyList;
  const evidence = website.evidence_urls || emptyList;
  const warnings = website.quality?.warnings || emptyList;

  return (
    <div className="border rounded bg-white p-3">
      <div className="d-flex justify-content-between align-items-start gap-3 mb-3 flex-wrap">
        <div>
          <div className="fw-bold">Website Intelligence Audit</div>
          <div className="text-muted small">
            {website.domain || audit.source_url || 'Website draft'} - {String(website.industry || audit.industry || 'unknown').replaceAll('_', ' ')}
          </div>
        </div>
        <div className="d-flex gap-2 flex-wrap">
          <span className="badge bg-light text-dark border text-capitalize">
            {website.quality?.level || 'unknown'} readiness
          </span>
          <span className="badge bg-warning-subtle text-warning border border-warning-subtle">
            Review required
          </span>
          {website.auto_publish === false && (
            <span className="badge bg-success-subtle text-success border border-success-subtle">
              Auto-publish off
            </span>
          )}
        </div>
      </div>

      <div className="row g-3 small">
        <div className="col-lg-5">
          <div className="fw-semibold mb-2">Review Checklist</div>
          {checklist.length ? (
            <div className="d-flex flex-column gap-1">
              {checklist.map((item, index) => (
                <div key={`${item.key || item.label}-${index}`} className="d-flex justify-content-between gap-2 border rounded px-2 py-1">
                  <span>{item.label || item.key}</span>
                  <span className={item.passed ? 'text-success fw-semibold' : 'text-warning fw-semibold'}>
                    {item.passed ? 'Pass' : 'Review'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-muted">No checklist stored.</div>
          )}
        </div>

        <div className="col-lg-4">
          <div className="fw-semibold mb-2">Evidence URLs</div>
          {evidence.length ? (
            <div className="d-flex flex-column gap-1">
              {evidence.map((url) => (
                <a key={url} href={url} target="_blank" rel="noreferrer" className="text-truncate">
                  {url}
                </a>
              ))}
            </div>
          ) : (
            <div className="text-muted">No source evidence stored.</div>
          )}
        </div>

        <div className="col-lg-3">
          <div className="fw-semibold mb-2">Quality</div>
          <div className="text-muted">Score {website.quality?.score ?? 0}/100</div>
          {warnings.length ? (
            <ul className="mt-2 mb-0 ps-3">
              {warnings.slice(0, 3).map((warning, index) => (
                <li key={`${warning}-${index}`}>{warning}</li>
              ))}
            </ul>
          ) : (
            <div className="text-muted mt-2">No warnings.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function GeneratedScriptReview({ review }) {
  if (!review) return null;
  return (
    <div className="border rounded bg-white p-3">
      <div className="d-flex justify-content-between align-items-start gap-3 flex-wrap">
        <div>
          <div className="fw-bold">Generated Draft Review</div>
          <div className="text-muted small">
            Draft {(review.draft_id || '').substring(0, 8)} saved to Flow V2 draft.
          </div>
        </div>
        <div className="d-flex gap-2 flex-wrap">
          <span className="badge bg-success-subtle text-success border border-success-subtle">
            {String(review.status || 'flow_draft_saved').replaceAll('_', ' ')}
          </span>
          {review.published_live === false && (
            <span className="badge bg-light text-dark border">Live runtime unchanged</span>
          )}
        </div>
      </div>
      <div className="small text-muted mt-2">
        Reviewed {review.reviewed_at || 'now'}
        {review.flow_version_id ? ` - Flow version ${(review.flow_version_id || '').substring(0, 8)}` : ''}
      </div>
    </div>
  );
}

function ReviewGatePolicy({ policy }) {
  if (!policy?.enabled) return null;
  const blockers = policy.blockers || emptyList;
  const warnings = policy.warnings || emptyList;
  return (
    <div className="border rounded bg-white p-3">
      <div className="d-flex justify-content-between align-items-start gap-3 flex-wrap">
        <div>
          <div className="fw-bold">Review Gate Shadow</div>
          <div className="text-muted small">
            Future enforcement preview only. Current save behavior is unchanged.
          </div>
        </div>
        <div className="d-flex gap-2 flex-wrap">
          <span className="badge bg-light text-dark border">Shadow only</span>
          <span className={`badge border ${policy.would_block_if_enforced ? 'bg-warning-subtle text-warning border-warning-subtle' : 'bg-success-subtle text-success border-success-subtle'}`}>
            {policy.would_block_if_enforced ? 'Would require review' : 'Would pass'}
          </span>
        </div>
      </div>
      <div className="row g-3 small mt-1">
        <div className="col-md-4">
          <div className="text-muted">Quality</div>
          <div className="fw-semibold text-capitalize">
            {policy.quality?.level || 'unknown'} - {policy.quality?.score ?? 0}/100
          </div>
        </div>
        <div className="col-md-4">
          <div className="text-muted">Checklist</div>
          <div className="fw-semibold">
            {policy.checklist?.failed ?? 0} failed / {policy.checklist?.total ?? 0} total
          </div>
        </div>
        <div className="col-md-4">
          <div className="text-muted">Runtime</div>
          <div className="fw-semibold">Live calls unchanged</div>
        </div>
        {blockers.length > 0 && (
          <div className="col-md-6">
            <div className="fw-semibold mb-1">Shadow blockers</div>
            <ul className="mb-0 ps-3">
              {blockers.map((item) => (
                <li key={item}>{String(item).replaceAll('_', ' ')}</li>
              ))}
            </ul>
          </div>
        )}
        {warnings.length > 0 && (
          <div className="col-md-6">
            <div className="fw-semibold mb-1">Warnings</div>
            <ul className="mb-0 ps-3">
              {warnings.slice(0, 4).map((item, index) => (
                <li key={`${item}-${index}`}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function splitCsv(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinCsv(values) {
  return (values || []).join(', ');
}

export default function FlowPreviewModal({ agent, preview, loading, error, onClose, onSave }) {
  const steps = preview?.conversation_preview || emptyList;
  const editable = preview?.editable_flow;
  const graphNodes = preview?.graph?.nodes || emptyList;
  const graphEdges = preview?.graph?.edges || emptyList;
  const nodeOptions = editable?.node_options?.length
    ? editable.node_options
    : graphNodes.map((node) => ({ id: node.id, label: node.label || node.id }));
  const editableNodes = editable?.nodes?.length
    ? editable.nodes
    : graphNodes.map((node) => ({
      id: node.id,
      type: node.type,
      label: node.label || node.id,
      response_en: node.agent_says || '',
      collects: node.collects || [],
      transitions: graphEdges
        .filter((edge) => edge.from === node.id)
        .map((edge) => ({
          intent: edge.intent || '',
          label: edge.label || edge.intent || '',
          target: edge.to || '',
        })),
    }));
  const canEditFlow = Boolean(onSave && editableNodes.length);
  const [editMode, setEditMode] = useState(false);
  const [draftNodes, setDraftNodes] = useState([]);
  const [insertAfterId, setInsertAfterId] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const draftNodeOptions = draftNodes.map((node) => ({ id: node.id, label: node.label || node.id, type: node.type }));
  const insertableNodeOptions = draftNodeOptions.filter((node) => node.type !== 'end');

  const updateNode = (nodeId, patch) => {
    setDraftNodes((nodes) => nodes.map((node) => (node.id === nodeId ? { ...node, ...patch } : node)));
  };

  const updateTransition = (nodeId, index, patch) => {
    setDraftNodes((nodes) => nodes.map((node) => {
      if (node.id !== nodeId) return node;
      const transitions = [...(node.transitions || [])];
      transitions[index] = { ...transitions[index], ...patch };
      return { ...node, transitions };
    }));
  };

  const handleSave = async () => {
    if (!onSave) return;
    setSaving(true);
    setSaveError('');
    try {
      await onSave({
        nodes: draftNodes.map((node) => ({
          id: node.id,
          type: node.type || 'message',
          label: node.label,
          response_en: node.response_en,
          collects: node.collects || [],
          transitions: node.transitions || [],
        })),
      });
      setEditMode(false);
    } catch (err) {
      setSaveError(err.message || 'Flow save failed');
    } finally {
      setSaving(false);
    }
  };

  const beginEdit = () => {
    const nodes = JSON.parse(JSON.stringify(editableNodes));
    setDraftNodes(nodes);
    setInsertAfterId((nodes.find((node) => node.type !== 'end') || nodes[0] || {}).id || '');
    setSaveError('');
    setEditMode(true);
  };

  const addNodeAfterSelection = () => {
    setDraftNodes((nodes) => {
      if (!nodes.length) return nodes;
      const selectedIndex = Math.max(0, nodes.findIndex((node) => node.id === insertAfterId));
      const selectedNode = nodes[selectedIndex] || nodes[0];
      if (!selectedNode || selectedNode.type === 'end') return nodes;
      const endNode = nodes.find((node) => node.type === 'end');
      const firstTransition = (selectedNode.transitions || [])[0];
      const oldTarget = firstTransition?.target || endNode?.id || selectedNode.id;
      const baseId = `custom_node_${nodes.length + 1}`;
      let newId = baseId;
      let suffix = 2;
      while (nodes.some((node) => node.id === newId)) {
        newId = `${baseId}_${suffix}`;
        suffix += 1;
      }
      const newNode = {
        id: newId,
        type: 'message',
        label: 'New Step',
        response_en: 'Add the agent question here.',
        collects: [],
        transitions: [{ intent: 'confirm', label: 'Continue', target: oldTarget }],
      };
      const updatedSelected = {
        ...selectedNode,
        transitions: selectedNode.transitions?.length
          ? selectedNode.transitions.map((transition, index) => (index === 0 ? { ...transition, target: newId } : transition))
          : [{ intent: 'confirm', label: 'Continue', target: newId }],
      };
      const updated = [...nodes];
      updated[selectedIndex] = updatedSelected;
      updated.splice(selectedIndex + 1, 0, newNode);
      setInsertAfterId(newId);
      return updated;
    });
  };

  return (
    <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
      <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
        <div className="modal-content border-0 shadow">
          <div className="modal-header">
            <div>
              <h5 className="modal-title fw-bold mb-1">Flow Preview</h5>
              <div className="text-muted small">{agent?.name || preview?.agent?.name || 'Voice Agent'}</div>
            </div>
            <div className="d-flex align-items-center gap-2">
              {canEditFlow && !editMode && (
                <button type="button" className="btn btn-outline-primary btn-sm" onClick={beginEdit}>
                  Edit Flow
                </button>
              )}
              <button type="button" className="btn-close shadow-none" onClick={onClose}></button>
            </div>
          </div>
          <div className="modal-body bg-light">
            {loading && (
              <div className="text-center py-5">
                <span className="spinner-border text-primary"></span>
              </div>
            )}

            {!loading && error && (
              <div className="alert alert-warning mb-0">{error}</div>
            )}

            {!loading && !error && preview && (
              <div className="d-flex flex-column gap-3">
                <div className="row g-3">
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Runtime</div>
                      <div className="fw-bold text-capitalize">{preview.runtime_mode}</div>
                    </div>
                  </div>
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Status</div>
                      <div className="fw-bold text-capitalize">{preview.status}</div>
                    </div>
                  </div>
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Nodes</div>
                      <div className="fw-bold">{preview.stats?.node_count || 0}</div>
                    </div>
                  </div>
                  <div className="col-md-3">
                    <div className="border rounded bg-white p-3 h-100">
                      <div className="text-muted small">Transitions</div>
                      <div className="fw-bold">{preview.stats?.edge_count || 0}</div>
                    </div>
                  </div>
                </div>

                {saveError && <div className="alert alert-warning mb-0">{saveError}</div>}
                <GeneratedScriptReview review={preview.generated_script_review} />
                <ReviewGatePolicy policy={preview.review_policy || preview.generated_script_review?.review_policy} />
                <AuditChecklist audit={preview.audit} />

                {editMode ? (
                  <div className="border rounded bg-white p-3">
                    <div className="d-flex justify-content-between align-items-end gap-3 mb-3 flex-wrap">
                      <div>
                        <div className="fw-bold">Edit Flow Draft</div>
                        <div className="text-muted small">New nodes are saved as draft flow only.</div>
                      </div>
                      <div className="d-flex align-items-end gap-2">
                        <div>
                          <label className="form-label small fw-semibold text-muted mb-1">Insert after</label>
                          <select
                            className="form-select form-select-sm"
                            value={insertAfterId}
                            onChange={(event) => setInsertAfterId(event.target.value)}
                          >
                            {insertableNodeOptions.map((option) => (
                              <option key={`insert-${option.id}`} value={option.id}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>
                        <button type="button" className="btn btn-outline-primary btn-sm" onClick={addNodeAfterSelection}>
                          Add Node
                        </button>
                      </div>
                    </div>
                    <div className="d-flex flex-column gap-3">
                      {draftNodes.map((node) => (
                        <div key={node.id} className="border rounded p-3">
                          <div className="row g-3">
                            <div className="col-md-4">
                              <label className="form-label small fw-semibold text-muted">Node label</label>
                              <input
                                className="form-control"
                                value={node.label || ''}
                                onChange={(event) => updateNode(node.id, { label: event.target.value })}
                              />
                            </div>
                            <div className="col-md-4">
                              <label className="form-label small fw-semibold text-muted">Type</label>
                              <input className="form-control" value={node.type || ''} disabled />
                            </div>
                            <div className="col-md-4">
                              <label className="form-label small fw-semibold text-muted">Collects</label>
                              <input
                                className="form-control"
                                value={joinCsv(node.collects)}
                                onChange={(event) => updateNode(node.id, { collects: splitCsv(event.target.value) })}
                                placeholder="budget, location"
                              />
                            </div>
                            <div className="col-12">
                              <label className="form-label small fw-semibold text-muted">Agent says</label>
                              <textarea
                                className="form-control"
                                rows="2"
                                value={node.response_en || ''}
                                onChange={(event) => updateNode(node.id, { response_en: event.target.value })}
                              />
                            </div>
                          </div>

                          {(node.transitions || []).length > 0 && (
                            <div className="mt-3">
                              <div className="small fw-semibold text-muted mb-2">Transitions</div>
                              <div className="d-flex flex-column gap-2">
                                {(node.transitions || []).map((transition, index) => (
                                  <div key={`${node.id}-${transition.intent}-${index}`} className="row g-2">
                                    <div className="col-md-3">
                                      <input
                                        className="form-control form-control-sm"
                                        value={transition.intent || ''}
                                        onChange={(event) => updateTransition(node.id, index, { intent: event.target.value })}
                                      />
                                    </div>
                                    <div className="col-md-5">
                                      <input
                                        className="form-control form-control-sm"
                                        value={transition.label || ''}
                                        onChange={(event) => updateTransition(node.id, index, { label: event.target.value })}
                                        placeholder="Expected response label"
                                      />
                                    </div>
                                    <div className="col-md-4">
                                      <select
                                        className="form-select form-select-sm"
                                        value={transition.target || ''}
                                        onChange={(event) => updateTransition(node.id, index, { target: event.target.value })}
                                      >
                                        {(draftNodeOptions.length ? draftNodeOptions : nodeOptions).map((option) => (
                                          <option key={`${node.id}-${index}-${option.id}`} value={option.id}>
                                            {option.label}
                                          </option>
                                        ))}
                                      </select>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="border rounded bg-white p-3">
                      <div className="fw-bold mb-3">Conversation Path</div>
                      <div className="d-flex flex-column gap-3">
                        {steps.map((step, index) => (
                          <div key={step.node_id} className="border rounded p-3">
                            <div className="d-flex justify-content-between align-items-start gap-3 mb-2">
                              <div>
                                <div className="fw-semibold">{index + 1}. {step.label}</div>
                                <div className="text-muted small text-capitalize">{step.type}</div>
                              </div>
                              {step.is_terminal && <span className="badge bg-success-subtle text-success border">Terminal</span>}
                            </div>
                            <div className="small mb-2">{step.agent_says}</div>
                            <div className="row g-2 small">
                              <div className="col-md-4">
                                <div className="text-muted mb-1">Expected responses</div>
                                <BadgeList values={step.expected_user_responses} />
                              </div>
                              <div className="col-md-4">
                                <div className="text-muted mb-1">Collects</div>
                                <BadgeList values={step.collects} />
                              </div>
                              <div className="col-md-4">
                                <div className="text-muted mb-1">Next</div>
                                <span className="fw-semibold">{step.next_node_id || 'None'}</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="row g-3">
                      <div className="col-md-6">
                        <PathList title="Fallback Paths" items={preview.fallback_paths} />
                      </div>
                      <div className="col-md-6">
                        <PathList title="Objection Paths" items={preview.objection_paths} />
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
          <div className="modal-footer">
            {canEditFlow && !editMode && (
              <button type="button" className="btn btn-outline-primary" onClick={beginEdit}>
                Edit Flow
              </button>
            )}
            {editMode && (
              <>
                <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
                  {saving ? 'Saving...' : 'Save Flow Draft'}
                </button>
                <button type="button" className="btn btn-light border" onClick={() => setEditMode(false)} disabled={saving}>
                  Cancel Edit
                </button>
              </>
            )}
            <button type="button" className="btn btn-light border" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  );
}
