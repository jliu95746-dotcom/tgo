import apiClient from './api';
import type { AgentBusinessCapabilities, BusinessCustomerTestSession, BusinessEmployeeCurrentState, BusinessRequestControl, BusinessRequestState, BusinessReviewRecord, BusinessReviewRequest, BusinessReviewRequestState, BusinessReviewState, BusinessTrialReport, BusinessTrialRequest } from '@/types/agentBusinessTrials';

const root=(agent:string)=>`/v1/ai/agents/${encodeURIComponent(agent)}`;
const report=(agent:string,id:string)=>`${root(agent)}/business-trials/${encodeURIComponent(id)}`;
const options=(signal?:AbortSignal)=>({signal});
export const AgentBusinessTrialsApi={
  currentEmployee:(agent:string,signal?:AbortSignal)=>apiClient.get<BusinessEmployeeCurrentState>(`${root(agent)}/business-current-state`,options(signal)),
  verifyCustomer:(connection:string,assertion:string,signal?:AbortSignal)=>apiClient.post<BusinessCustomerTestSession>(`/v1/business-connections/${encodeURIComponent(connection)}/customer-identities/exchange`,{merchant_assertion:assertion},options(signal)),
  start:(agent:string,body:BusinessTrialRequest,signal?:AbortSignal)=>apiClient.post<BusinessTrialReport>(`${root(agent)}/business-trials`,body,options(signal)),
  recover:(agent:string,request:string,signal?:AbortSignal)=>apiClient.get<BusinessTrialReport>(`${root(agent)}/business-trials/requests/${encodeURIComponent(request)}`,options(signal)),
  requestState:(agent:string,request:string,signal?:AbortSignal)=>apiClient.get<BusinessRequestState>(`${root(agent)}/business-trials/requests/${encodeURIComponent(request)}/state`,options(signal)),
  cancelRequest:(agent:string,body:BusinessRequestControl,signal?:AbortSignal)=>apiClient.post<BusinessRequestState>(`${root(agent)}/business-trials/requests/${encodeURIComponent(body.request_id)}/cancel`,body,options(signal)),
  read:(agent:string,id:string,signal?:AbortSignal)=>apiClient.get<BusinessTrialReport>(report(agent,id),options(signal)),
  cancel:(agent:string,id:string,signal?:AbortSignal)=>apiClient.post<BusinessTrialReport>(`${report(agent,id)}/cancel`,undefined,options(signal)),
  reviewState:(agent:string,id:string,signal?:AbortSignal)=>apiClient.get<BusinessReviewState>(`${report(agent,id)}/reviews`,options(signal)),
  review:(agent:string,id:string,body:BusinessReviewRequest,signal?:AbortSignal)=>apiClient.post<BusinessReviewRecord>(`${report(agent,id)}/reviews`,body,options(signal)),
  recoverReview:(agent:string,id:string,request:string,signal?:AbortSignal)=>apiClient.get<BusinessReviewRecord>(`${report(agent,id)}/reviews/requests/${encodeURIComponent(request)}`,options(signal)),
  reviewRequestState:(agent:string,id:string,request:string,signal?:AbortSignal)=>apiClient.get<BusinessReviewRequestState>(`${report(agent,id)}/reviews/requests/${encodeURIComponent(request)}/state`,options(signal)),
  resumeReview:(agent:string,id:string,request:string,signal?:AbortSignal)=>apiClient.post<BusinessReviewRecord>(`${report(agent,id)}/reviews/requests/${encodeURIComponent(request)}/resume`,undefined,options(signal)),
  cancelReviewRequest:(agent:string,id:string,request:string,signal?:AbortSignal)=>apiClient.post<BusinessReviewRequestState>(`${report(agent,id)}/reviews/requests/${encodeURIComponent(request)}/cancel`,undefined,options(signal)),
  capabilities:(agent:string,signal?:AbortSignal)=>apiClient.get<AgentBusinessCapabilities>(`${root(agent)}/business-capabilities`,options(signal)),
};
