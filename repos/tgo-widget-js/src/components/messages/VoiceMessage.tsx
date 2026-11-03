import { useTranslation } from 'react-i18next'
import { useChatFileUrls } from '../../store/chatFileAccess'

export default function VoiceMessage({ url }: { url: string }) {
  const { t } = useTranslation()
  const [accessUrl] = useChatFileUrls([url])

  return (
    <div style={{ maxWidth: 'var(--bubble-max-width, 380px)', padding: 12,
      border: '1px solid var(--border-primary, #e5e7eb)', borderRadius: 12,
      background: 'var(--bg-primary, #fff)', color: 'var(--text-primary, #111827)' }}>
      <p style={{ fontSize: 12, margin: '0 0 8px' }}>{t('voice.label')}</p>
      {accessUrl ? (
        <audio controls preload="none" src={accessUrl} aria-label={t('voice.play')}
          style={{ width: 240, maxWidth: '100%' }} />
      ) : (
        <p style={{ fontSize: 12, margin: 0, color: 'var(--text-secondary, #6b7280)' }}>
          {t('voice.unavailable')}
        </p>
      )}
    </div>
  )
}
