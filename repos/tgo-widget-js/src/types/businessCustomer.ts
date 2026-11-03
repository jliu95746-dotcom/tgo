/** A merchant-signed customer session, kept only for the current request. */
export type BusinessCustomerSessionInput = Readonly<{
  connection_id: string
  customer_session: string
}>

export type BusinessCustomerAssertionInput = Readonly<{
  connection_id: string
  merchant_assertion: string
}>

export type BusinessCustomerIdentityInput = BusinessCustomerSessionInput | BusinessCustomerAssertionInput

export type BusinessCustomerSessionContext = Readonly<{
  visitorId: string
  projectId: string
  platformId: string
  sourceMessageId?: string
}>

export type BusinessChatIdentity = Readonly<{
  apiBase: string
  platformApiKey: string
  fromUid: string
  channelId?: string
  channelType?: number
  visitorId?: string
  projectId?: string
  platformId?: string
}>

export type BusinessCustomerSessionProvider = (
  context: BusinessCustomerSessionContext,
  options: Readonly<{ signal: AbortSignal }>,
) => Promise<readonly BusinessCustomerIdentityInput[]>
