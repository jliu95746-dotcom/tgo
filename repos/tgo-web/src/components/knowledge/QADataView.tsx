import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Loader2,
    Search,
    MessageSquare,
    FileText,
    X,
    ExternalLink,
    ChevronDown,
    ChevronUp
} from 'lucide-react';
import { KnowledgeBaseApiService } from '@/services/knowledgeBaseApi';
import { useAuthStore } from '@/stores/authStore';

interface QADataViewProps {
    fileId: string;
    fileName: string;
    onClose: () => void;
}

export const QADataView: React.FC<QADataViewProps> = ({ fileId, fileName, onClose }) => {
    const { t } = useTranslation();
    const [segments, setSegments] = useState<any[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [expandedId, setExpandedId] = useState<string | null>(null);

    const projectId = useAuthStore(state => state.user?.project_id);

    const loadSegments = useCallback(async () => {
        if (!fileId || !projectId) return;
        setIsLoading(true);
        try {
            const response = await KnowledgeBaseApiService.getFileDocuments(fileId, {
                project_id: projectId,
                limit: 100
            });
            setSegments(response.data || []);
        } catch (error) {
            console.error('Failed to load segments:', error);
        } finally {
            setIsLoading(false);
        }
    }, [fileId, projectId]);

    useEffect(() => {
        loadSegments();
    }, [loadSegments]);

    const filteredSegments = segments.filter(seg =>
        seg.content.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const toggleExpand = (id: string) => {
        setExpandedId(expandedId === id ? null : id);
    };

    return (
        <div className="flex flex-col h-full bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/50">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg text-blue-600 dark:text-blue-400">
                        <MessageSquare className="h-5 w-5" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-gray-900 dark:text-white truncate max-w-[400px]">
                            {fileName}-{t('knowledge.qa.results', 'QA 问答对')}
                        </h2>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            {t('knowledge.qa.segmentCount', '共 {{count}} 个问答对', { count: segments.length })}
                        </p>
                    </div>
                </div>
                <button
                    onClick={onClose}
                    className="p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition-colors"
                >
                    <X className="h-5 w-5 text-gray-500" />
                </button>
            </div>

            {/* Search Bar */}
            <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-800">
                <div className="relative">
                    <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
                    <input
                        type="text"
                        placeholder={t('knowledge.qa.searchSegments', '搜索分段内容...')}
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="w-full pl-10 pr-4 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    />
                </div>
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
                {isLoading ? (
                    <div className="flex flex-col items-center justify-center h-full py-12">
                        <Loader2 className="h-8 w-8 animate-spin text-blue-500 mb-4" />
                        <p className="text-gray-500">{t('knowledge.qa.loadingSegments', '正在加载分段成果...')}</p>
                    </div>
                ) : filteredSegments.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full py-12 text-center">
                        <div className="p-4 bg-gray-100 dark:bg-gray-800 rounded-full mb-4">
                            <FileText className="h-8 w-8 text-gray-400" />
                        </div>
                        <p className="text-gray-900 dark:text-white font-medium">
                            {searchTerm ? t('knowledge.qa.noResults', '未找到匹配的分段') : t('knowledge.qa.noSegments', '该文件暂无问答分段成果')}
                        </p>
                        <p className="text-sm text-gray-500 mt-1">
                            {searchTerm ? t('knowledge.qa.tryDifferentSearch', '请尝试换个关键词') : t('knowledge.qa.ensureQaMode', '请确保上传时开启了 QA 拆分模式')}
                        </p>
                    </div>
                ) : (
                    filteredSegments.map((seg, idx) => {
                        // More robust QA detection: check content_type, tags.is_qa, or content pattern
                        const isQA =
                            seg.content_type === 'qa_pair' ||
                            seg.content_type === 'qa' ||
                            (seg.tags && seg.tags.is_qa) ||
                            (seg.content && seg.content.includes('Question:') && seg.content.includes('Answer:'));

                        const content = seg.content || '';
                        let question = '';
                        let answer = '';

                        if (isQA) {
                            const qPart = content.match(/Question:\s*(.*)\nAnswer:/s);
                            const aPart = content.match(/Answer:\s*(.*)/s);
                            question = qPart ? qPart[1].trim() : (seg.tags?.original_question || '');
                            answer = aPart ? aPart[1].trim() : (seg.tags?.original_answer || content);
                        }

                        const isExpanded = expandedId === seg.id;

                        return (
                            <div
                                key={seg.id}
                                className="group bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden hover:border-blue-300 dark:hover:border-blue-700 transition-all shadow-sm hover:shadow-md"
                            >
                                <div
                                    className="px-5 py-4 cursor-pointer"
                                    onClick={() => toggleExpand(seg.id)}
                                >
                                    <div className="flex items-start justify-between gap-4">
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 mb-2">
                                                <span className="flex-shrink-0 px-2 py-0.5 bg-blue-50 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 text-[10px] font-bold rounded uppercase tracking-wider">
                                                    # {idx + 1}
                                                </span>
                                                <span className={`flex-shrink-0 px-2 py-0.5 ${isQA ? 'bg-green-50 dark:bg-green-900/40 text-green-600 dark:text-green-400' : 'bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400'} text-[10px] font-bold rounded uppercase tracking-wider`}>
                                                    {isQA ? 'QA PAIR' : 'TEXT SEGMENT'}
                                                </span>
                                            </div>
                                            <h3 className={`text-sm font-semibold text-gray-900 dark:text-white ${!isExpanded ? 'truncate' : ''}`}>
                                                {isQA ? question : `${content.slice(0, 100)  }...`}
                                            </h3>
                                        </div>
                                        <div className="flex-shrink-0 text-gray-400 group-hover:text-blue-500 transition-colors">
                                            {isExpanded ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
                                        </div>
                                    </div>
                                </div>

                                {isExpanded && (
                                    <div className="px-5 pb-5 pt-1 border-t border-gray-100 dark:border-gray-800 animate-in slide-in-from-top-2 duration-200">
                                        {isQA ? (
                                            <div className="space-y-4">
                                                <div>
                                                    <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-1.5 flex items-center gap-1.5">
                                                        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full"></span>
                                                        Question
                                                    </p>
                                                    <div className="p-3 bg-blue-50/30 dark:bg-blue-900/10 rounded-lg text-sm text-gray-800 dark:text-gray-200 leading-relaxed border border-blue-100/30 dark:border-blue-900/20">
                                                        {question}
                                                    </div>
                                                </div>
                                                <div>
                                                    <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-1.5 flex items-center gap-1.5">
                                                        <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                                                        Answer
                                                    </p>
                                                    <div className="p-3 bg-green-50/30 dark:bg-green-900/10 rounded-lg text-sm text-gray-800 dark:text-gray-200 leading-relaxed border border-green-100/30 dark:border-green-900/20">
                                                        {answer}
                                                    </div>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="p-3 bg-gray-50 dark:bg-gray-900 rounded-lg text-sm text-gray-800 dark:text-gray-200 leading-relaxed border border-gray-100 dark:border-gray-800">
                                                {content}
                                            </div>
                                        )}

                                        <div className="mt-4 flex items-center justify-between">
                                            <div className="flex items-center gap-4 text-xs text-gray-400">
                                                <span>{seg.token_count || 0} Tokens</span>
                                                <span>{content.length} {t('knowledge.qa.chars', '字符')}</span>
                                            </div>
                                            <button className="flex items-center gap-1.5 text-xs text-blue-500 hover:text-blue-600 font-medium transition-colors">
                                                <ExternalLink className="h-3 w-3" />
                                                {t('knowledge.qa.editSegment', '编辑段落')}
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })
                )}
            </div>

            {/* Footer Area */}
            <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/50 flex justify-end">
                <button
                    onClick={onClose}
                    className="px-6 py-2 bg-gray-900 dark:bg-white text-white dark:text-gray-900 rounded-lg text-sm font-semibold hover:bg-gray-800 dark:hover:bg-gray-100 transition-all shadow-sm"
                >
                    {t('common.close', '关闭')}
                </button>
            </div>
        </div>
    );
};
