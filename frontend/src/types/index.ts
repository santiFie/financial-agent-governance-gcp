export interface DetectedPII {
  info_type: string;
  token_assigned: string;
  start_offset?: number;
  end_offset?: number;
}

export interface SecurityMetadata {
  sanitized_input: string;
  pii_detected: DetectedPII[];
  prompt_injection_flagged: boolean;
  threat_category?: string;
  defense_action: 'PASSED' | 'DEIDENTIFIED' | 'BLOCKED';
  processing_time_ms: number;
}

export interface PendingTransferInfo {
  transaction_id: string;
  session_id: string;
  user_id: string;
  amount: number;
  currency: string;
  source_account: string;
  target_token: string;
  status: 'PENDING_APPROVAL' | 'APPROVED' | 'REJECTED' | 'EXECUTED';
  created_at: string;
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  timestamp: string;
  status?: 'COMPLETED' | 'BLOCKED' | 'PENDING_APPROVAL';
  security_audit?: SecurityMetadata;
  pending_transfer?: PendingTransferInfo;
}

export interface BankAccount {
  account_id: string;
  account_number: string;
  owner_name: string;
  balance: number;
  currency: string;
}

export interface BankTransaction {
  tx_id: string;
  account_id: string;
  type: string;
  amount: number;
  description: string;
  timestamp: string;
}

export interface HealthStatus {
  status: string;
  environment: string;
  security_mode: string;
  checkpointer: string;
  gcp_project: string;
}
