import { useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useChatStore } from '../store'

const Notice = styled.div`
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 7px 12px; border-bottom: 1px solid var(--border-secondary, #eef2f4);
  background: var(--bg-tertiary, #f3f4f6);
  color: var(--text-secondary, #6b7280); font-size: 12px; line-height: 1.4;
`

const TransferButton = styled.button`
  flex: none; border: 0; border-radius: 6px; padding: 5px 8px;
  background: var(--bg-primary, #fff); color: var(--primary, #2f80ed);
  font: inherit; font-weight: 600; cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover, #e5e7eb); }
  &:disabled { opacity: .55; cursor: not-allowed; }
`

export default function CustomerServiceNotice({ serviceAvailable }: { serviceAvailable: boolean | null }) {
  const { t } = useTranslation()
  const sendMessage = useChatStore(state => state.sendMessage)
  const online = useChatStore(state => state.online)
  const myUid = useChatStore(state => state.myUid)
  const [sending, setSending] = useState(false)

  const requestHumanService = async () => {
    if (sending || !online || !myUid || serviceAvailable === false) return
    setSending(true)
    try {
      await sendMessage('转人工')
    } finally {
      setSending(false)
    }
  }

  return (
    <Notice>
      <span>{t('customerService.aiDisclosure')}</span>
      <TransferButton
        type="button"
        disabled={sending || !online || !myUid || serviceAvailable === false}
        onClick={() => { void requestHumanService() }}
      >
        {t(sending ? 'customerService.requestingHuman' : 'customerService.requestHuman')}
      </TransferButton>
    </Notice>
  )
}
