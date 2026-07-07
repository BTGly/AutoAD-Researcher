from fastapi import APIRouter
from autoad_researcher.server.models import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/send", response_model=ChatResponse)
async def chat_send(req: ChatRequest):
    text = req.user_input.strip()

    if not text:
        return ChatResponse(reply="请输入问题。", reply_kind="answer")

    if any(kw in text for kw in ("搜索", "搜一下", "最新", "找论文")):
        return ChatResponse(
            reply=(
                "当前暂无可用搜索后端。\n\n"
                "你可以：\n"
                "- 上传 PDF 论文（点击右下角 +）\n"
                "- 粘贴 arXiv / GitHub 链接到对话框\n"
                "- 描述你的研究方向"
            ),
            reply_kind="candidate_only",
        )

    if "http" in text or "arxiv" in text.lower() or "github.com" in text.lower():
        kind = "github_repo" if "github.com" in text.lower() else "webpage"
        return ChatResponse(
            reply=f"已接收链接（{kind}）。正在后台处理…完成后弹出通知。",
            reply_kind="need_acquire",
        )

    return ChatResponse(
        reply=(
            "收到。\n\n"
            "当前暂无已解析的资料。上传 PDF 或粘贴链接开始分析。\n"
            "也可以描述你的研究方向，我会帮你整理资料。"
        ),
        reply_kind="answer",
    )
