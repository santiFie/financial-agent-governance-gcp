import React, { useState } from 'react';
import { UserCheck, Check, X, ShieldAlert, Clock } from 'lucide-react';
import { PendingTransferInfo } from '../types';

interface AdminDashboardProps {
  pendingTransfers: PendingTransferInfo[];
  onApprove: (sessionId: string, txId: string, decision: 'APPROVED' | 'REJECTED', reason?: string) => Promise<void>;
  onRefresh: () => void;
  loading: boolean;
}

export const AdminDashboard: React.FC<AdminDashboardProps> = ({
  pendingTransfers,
  onApprove,
  onRefresh,
  loading,
}) => {
  const [selectedTx, setSelectedTx] = useState<PendingTransferInfo | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const handleAction = async (decision: 'APPROVED' | 'REJECTED') => {
    if (!selectedTx) return;
    setActionLoading(true);
    try {
      await onApprove(selectedTx.session_id, selectedTx.transaction_id, decision, rejectReason);
      setSelectedTx(null);
      setRejectReason('');
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div style={{ margin: '0 16px 16px 16px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      
      {/* Top Banner / Explanation of Governance */}
      <div className="glass-panel" style={{ padding: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div style={{
                width: '36px',
                height: '36px',
                borderRadius: '10px',
                background: 'rgba(245, 158, 11, 0.2)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
              }}>
                <UserCheck size={20} color="#fbbf24" />
              </div>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 800 }}>
                Portal de Oficial de Cumplimiento & Autorizaciones HITL
              </h2>
            </div>
            <p style={{ fontSize: '0.85rem', color: '#94a3b8', marginTop: '6px' }}>
              Aquí convergen las solicitudes destructivas (débito/transferencia) pausadas por los 
              <strong> breakpoints de LangGraph</strong>. Ninguna transferencia se ejecuta sin autorización explícita.
            </p>
          </div>

          <button onClick={onRefresh} disabled={loading} className="btn-primary" style={{ padding: '8px 16px', fontSize: '0.85rem' }}>
            Actualizar Solicitudes
          </button>
        </div>
      </div>

      {/* Main Content: Pending Table */}
      <div className="glass-panel" style={{ padding: '24px' }}>
        <h3 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Clock size={18} color="#818cf8" />
          Transferencias en Espera de Aprobación ({pendingTransfers.length})
        </h3>

        {pendingTransfers.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '48px 20px',
            color: '#64748b',
            background: 'rgba(15, 23, 42, 0.4)',
            borderRadius: '12px',
            border: '1px dashed var(--border-subtle)'
          }}>
            <p style={{ fontSize: '0.95rem', fontWeight: 600, color: '#94a3b8' }}>
              No hay transferencias pendientes de revisión.
            </p>
            <p style={{ fontSize: '0.8rem', marginTop: '4px' }}>
              Pide una transferencia en la solapa de <strong>Cliente Banca</strong> para verla aparecer aquí en tiempo real.
            </p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)', textAlign: 'left', color: '#94a3b8' }}>
                  <th style={{ padding: '12px 14px' }}>ID Transacción</th>
                  <th style={{ padding: '12px 14px' }}>Cuenta Origen</th>
                  <th style={{ padding: '12px 14px' }}>Monto Solicitado</th>
                  <th style={{ padding: '12px 14px' }}>Destino Protegido (DLP Token)</th>
                  <th style={{ padding: '12px 14px' }}>Fecha/Hora</th>
                  <th style={{ padding: '12px 14px' }}>Acción</th>
                </tr>
              </thead>
              <tbody>
                {pendingTransfers.map((tx) => (
                  <tr key={tx.transaction_id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', color: '#818cf8', fontWeight: 600 }}>
                      {tx.transaction_id}
                    </td>
                    <td style={{ padding: '12px 14px', color: '#cbd5e1' }}>
                      {tx.source_account}
                    </td>
                    <td style={{ padding: '12px 14px', fontWeight: 700, color: '#34d399' }}>
                      ${tx.amount.toLocaleString('es-AR', { minimumFractionDigits: 2 })} {tx.currency}
                    </td>
                    <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', color: '#38bdf8' }}>
                      {tx.target_token}
                    </td>
                    <td style={{ padding: '12px 14px', color: '#94a3b8', fontSize: '0.75rem' }}>
                      {tx.created_at.replace('T', ' ').substring(0, 19)}
                    </td>
                    <td style={{ padding: '12px 14px' }}>
                      <button
                        onClick={() => setSelectedTx(tx)}
                        className="btn-primary"
                        style={{ padding: '6px 14px', fontSize: '0.75rem' }}
                      >
                        Revisar →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Review Modal */}
      {selectedTx && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(5, 8, 15, 0.85)',
          backdropFilter: 'blur(8px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999,
          padding: '20px'
        }}>
          <div className="glass-panel" style={{
            maxWidth: '560px',
            width: '100%',
            padding: '28px',
            border: '1px solid rgba(245, 158, 11, 0.4)',
            boxShadow: '0 20px 50px rgba(0,0,0,0.6)'
          }}>
            <h3 style={{ fontSize: '1.15rem', fontWeight: 700, marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <ShieldAlert color="#fbbf24" size={20} />
              Revisión de Cumplimiento (Human-in-the-Loop)
            </h3>

            <div style={{
              background: 'rgba(15, 23, 42, 0.8)',
              padding: '16px',
              borderRadius: '10px',
              border: '1px solid var(--border-subtle)',
              marginBottom: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              fontSize: '0.85rem'
            }}>
              <div><strong>ID Transacción:</strong> <span style={{ fontFamily: 'var(--font-mono)', color: '#818cf8' }}>{selectedTx.transaction_id}</span></div>
              <div><strong>ID de Sesión (LangGraph Thread):</strong> <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: '#94a3b8' }}>{selectedTx.session_id}</span></div>
              <div><strong>Monto:</strong> <span style={{ color: '#34d399', fontWeight: 700 }}>${selectedTx.amount.toLocaleString('es-AR', { minimumFractionDigits: 2 })} {selectedTx.currency}</span></div>
              <div><strong>Destinatario Anonimizado:</strong> <span style={{ fontFamily: 'var(--font-mono)', color: '#38bdf8' }}>{selectedTx.target_token}</span></div>
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ fontSize: '0.8rem', color: '#94a3b8', display: 'block', marginBottom: '6px' }}>
                Nota u observación de auditoría (opcional si aprueba, requerida si rechaza):
              </label>
              <input
                type="text"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Ej. Verificación telefónica completada con cliente..."
                style={{
                  width: '100%',
                  background: 'rgba(11, 16, 28, 0.95)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '8px',
                  padding: '10px 14px',
                  color: '#ffffff',
                  fontSize: '0.85rem',
                  outline: 'none'
                }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <button
                onClick={() => setSelectedTx(null)}
                disabled={actionLoading}
                style={{
                  background: 'transparent',
                  border: '1px solid var(--border-subtle)',
                  color: '#94a3b8',
                  borderRadius: '8px',
                  padding: '8px 16px',
                  cursor: 'pointer'
                }}
              >
                Cancelar
              </button>

              <div style={{ display: 'flex', gap: '10px' }}>
                <button
                  onClick={() => handleAction('REJECTED')}
                  disabled={actionLoading}
                  className="btn-danger"
                >
                  <X size={16} /> Rechazar
                </button>

                <button
                  onClick={() => handleAction('APPROVED')}
                  disabled={actionLoading}
                  className="btn-success"
                >
                  <Check size={16} /> Aprobar y Reanudar Grafo
                </button>
              </div>
            </div>

          </div>
        </div>
      )}

    </div>
  );
};
