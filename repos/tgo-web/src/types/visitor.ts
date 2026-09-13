import type { Visitor } from '@/types';

export interface CustomAttribute {
  id: string;
  key: string;
  value: string;
  editable?: boolean;
}

export interface VisitorBasicInfo {
  name: string;
  email: string;
  phone: string;
  nickname?: string;
  company?: string;
  jobTitle?: string;
  source?: string;
  note?: string;
  avatarUrl?: string;
  lastOnlineDurationMinutes?: number | null;
  customAttributes?: CustomAttribute[];
}

export interface VisitorEmotion {
  type: 'positive' | 'neutral' | 'negative';
  icon: string;
  label: string;
}

export interface VisitorAIInsights {
  satisfaction: number; // out of 5
  emotion: VisitorEmotion;
}

export interface VisitorSystemInfo {
  firstVisit: string;
  source: string;
  browser: string;
}

export interface VisitorActivity {
  icon: string;
  action: string;
}

export interface VisitorTicket {
  id: string;
  title: string;
  icon: string;
}

export interface AIPersonaTag {
  type: 'interest' | 'identity' | 'preference' | 'behavior';
  label: string;
}

export interface VisitorTag {
  id: string;
  display_name?: string;
  name: string;
  color: string;
  weight: number; // 1-10, 10为最高权重
  createdAt?: string;
}

export interface ExtendedVisitor extends Omit<Visitor, 'tags'> {
  platform: string;
  firstVisit: string;
  visitCount: number;
  tags: VisitorTag[];
  basicInfo: VisitorBasicInfo;
  aiInsights: VisitorAIInsights;
  systemInfo: VisitorSystemInfo;
  recentActivity: VisitorActivity[];
  relatedTickets: VisitorTicket[];
  aiPersonaTags?: AIPersonaTag[];
}
