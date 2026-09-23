import { Download } from 'lucide-react'
import { getFileIcon } from '../../utils/fileIcon'
import { formatFileSize } from './messageUtils'
import { FileAction, FileCard, FileIconBox, FileInfo, FileName, FileSize } from './messageStyles'
import { useChatFileUrls } from '../../store/chatFileAccess'

export interface FileMessageProps {
  url: string
  name: string
  size: number
}

export default function FileMessage({ url, name, size }: FileMessageProps){
  const [accessUrl] = useChatFileUrls([url])
  const icon = getFileIcon(name, undefined, 40)
  const open = ()=>{ try { if (accessUrl) window.open(accessUrl, '_blank', 'noopener,noreferrer') } catch {} }
  const label = `下载文件：${name}`
  return (
    <FileCard onClick={open} role="button" aria-label={label} title={label}>
      <FileIconBox aria-hidden>{icon}</FileIconBox>
      <FileInfo>
        <FileName title={name}>{name}</FileName>
        <FileSize>{formatFileSize(size)}</FileSize>
      </FileInfo>
      <FileAction href={accessUrl || undefined} target="_blank" rel="noreferrer" download onClick={e=>e.stopPropagation()} aria-label="下载">
        <Download size={20} />
      </FileAction>
    </FileCard>
  )
}

