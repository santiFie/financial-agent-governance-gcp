import { HealthStatus, PendingTransferInfo } from '../types';

const API_BASE = '/api';

export async function checkHealth(): Promise<HealthStatus> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error('Health check failed');
  return res.json();
}

export async function sendChatMessage(
  message: string,
  sessionId: string,
  userId: string = 'user_default_01'
) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      user_id: userId,
    }),
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({ detail: 'Network error' }));
    throw new Error(errorData.detail || 'Error en el servicio de chat');
  }

  return res.json();
}

export async function fetchPendingTransfers(): Promise<PendingTransferInfo[]> {
  const res = await fetch(`${API_BASE}/pending-transfers`);
  if (!res.ok) throw new Error('Failed to fetch pending transfers');
  return res.json();
}

export async function approveTransfer(
  sessionId: string,
  transactionId: string,
  decision: 'APPROVED' | 'REJECTED',
  adminUser: string = 'compliance_officer_santi',
  reason?: string
) {
  const res = await fetch(`${API_BASE}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      transaction_id: transactionId,
      decision,
      admin_user: adminUser,
      reason,
    }),
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({ detail: 'Approval failed' }));
    throw new Error(errorData.detail || 'Error en la aprobación de la transferencia');
  }

  return res.json();
}

export async function fetchUserAccounts(userId: string = 'user_default_01') {
  const res = await fetch(`${API_BASE}/accounts/${userId}`);
  if (!res.ok) throw new Error('Failed to fetch accounts');
  return res.json();
}
