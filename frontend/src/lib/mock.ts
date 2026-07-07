import type { WSMessage } from './types';

export function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export const mockParseFlow = (fileName: string): { events: WSMessage[]; reply: string } => ({
  events: [
    { type: 'source.created', sourceId: 'src_001', kind: 'paper_pdf', sourceLabel: fileName, delay: 200 },
    { type: 'job.started', jobId: 'job_001', jobType: 'paper_parse', sourceLabel: fileName, delay: 400 },
    { type: 'job.completed', jobId: 'job_001', jobType: 'paper_parse', duration: '2.1s', delay: 1800 },
    { type: 'subagent.completed', kind: 'paper_parse', message: `解析完成 · ${fileName}`, toast: true, delay: 100 },
    { type: 'assistant.delta', content: '已解析论文。', delay: 500 },
    { type: 'assistant.delta', content: '基于 paper_brief.md：\n\n', delay: 600 },
    { type: 'assistant.delta', content: '**SimpleNet** (arXiv 2303.15140v2) 提出 **Feature Adapter**——在预训练 backbone 后插入轻量级适配层，将特征映射到异常检测空间。配合 Gaussian 距离度量取代余弦相似度。\n\n', delay: 800 },
    { type: 'assistant.delta', content: '**可迁移性评估**：\n', delay: 300 },
    { type: 'assistant.delta', content: '- Feature Adapter ✅ 可在不改变 PatchCore memory bank / coreset / scoring 的前提下接入\n', delay: 400 },
    { type: 'assistant.delta', content: '- Gaussian Scoring ⚠️ 可选替代 cosine distance，需要 ablation\n', delay: 300 },
    { type: 'assistant.delta', content: '- Truncated Loss ❌ PatchCore 无训练阶段，不适用\n', delay: 300 },
    { type: 'assistant.done', delay: 100 },
  ],
  reply: '',
});

export const mockUrlFlow = (url: string): { events: WSMessage[]; reply: string } => {
  const isGitHub = url.includes('github.com');
  const label = isGitHub ? url.split('/').pop() || 'repo' : url.slice(0, 45);

  return {
    events: isGitHub
      ? [
          { type: 'source.created', sourceId: 'src_002', kind: 'github_repo', sourceLabel: label, delay: 200 },
          { type: 'job.started', jobId: 'job_002', jobType: 'git_clone', sourceLabel: label, delay: 400 },
          { type: 'job.completed', jobId: 'job_002', jobType: 'git_clone', duration: '3.4s', delay: 2000 },
          { type: 'job.started', jobId: 'job_003', jobType: 'repo_analyze', sourceLabel: label, delay: 200 },
          { type: 'job.completed', jobId: 'job_003', jobType: 'repo_analyze', duration: '1.1s', delay: 1000 },
          { type: 'subagent.completed', kind: 'repo_analyze', message: `分析完成 · ${label}`, toast: true, delay: 100 },
          { type: 'assistant.delta', content: `**${label}** clone + 分析完成。repo_brief.md 已生成：\n\n`, delay: 400 },
          { type: 'assistant.delta', content: '- `patchcore/patchcore.py` — 主流程\n', delay: 200 },
          { type: 'assistant.delta', content: '- `patchcore/backbones.py` — 特征提取器注册 (ResNet/WideResNet)\n', delay: 200 },
          { type: 'assistant.delta', content: '- `patchcore/sampler.py` — coreset 采样 (greedy approx)\n', delay: 200 },
          { type: 'assistant.delta', content: '- `patchcore/common.py` — 数据集加载 / 预处理\n', delay: 200 },
          { type: 'assistant.delta', content: '\n关键接口：`PatchCore.fit(train_loader)`, `PatchCore.predict(test_loader)`\n', delay: 300 },
          { type: 'assistant.done', delay: 100 },
        ]
      : [
          { type: 'source.created', sourceId: 'src_003', kind: 'webpage', sourceLabel: label, delay: 200 },
          { type: 'job.started', jobId: 'job_004', jobType: 'web_fetch', sourceLabel: label, delay: 400 },
          { type: 'job.completed', jobId: 'job_004', jobType: 'web_fetch', duration: '1.8s', delay: 1500 },
          { type: 'job.started', jobId: 'job_005', jobType: 'paper_parse', sourceLabel: label, delay: 200 },
          { type: 'job.completed', jobId: 'job_005', jobType: 'paper_parse', duration: '1.6s', delay: 1200 },
          { type: 'subagent.completed', kind: 'paper_parse', message: `解析完成 · ${label}`, toast: true, delay: 100 },
          { type: 'assistant.delta', content: `URL 内容已下载并解析。paper_brief.md 已生成。\n\n你可以问我论文内容、可迁移方法或研究方案。`, delay: 500 },
          { type: 'assistant.done', delay: 100 },
        ],
    reply: '',
  };
};

export const mockSearchFlow = (query: string): { events: WSMessage[]; reply: string } => ({
  events: [
    { type: 'job.started', jobId: 'job_006', jobType: 'web_search', sourceLabel: query.slice(0, 30), delay: 400 },
    { type: 'job.completed', jobId: 'job_006', jobType: 'web_search', duration: '0.8s', delay: 800 },
    { type: 'subagent.completed', kind: 'web_search', message: '找到 5 个候选来源', toast: true, delay: 100 },
    { type: 'assistant.delta', content: '找到 5 个候选来源（candidate_source_only）：\n\n', delay: 300 },
    { type: 'assistant.delta', content: '1. DINOv2 + PatchCore — GitHub\n', delay: 150 },
    { type: 'assistant.delta', content: '2. EfficientAD — arXiv 2303.05165\n', delay: 150 },
    { type: 'assistant.delta', content: '3. Anomalib — GitHub\n', delay: 150 },
    { type: 'assistant.delta', content: '4. PatchCore 原文 — arXiv 2106.08265\n', delay: 150 },
    { type: 'assistant.delta', content: '5. FastRecon — arXiv 2304.05189\n', delay: 150 },
    { type: 'assistant.done', delay: 100 },
  ],
  reply: '',
});
