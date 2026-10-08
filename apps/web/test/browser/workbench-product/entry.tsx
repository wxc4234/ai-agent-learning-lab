import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ChatPanel from "../../../src/features/chat/components/chat-panel";

// 使用实际工作台、任务导航与详情portal；受控数据只由隔离浏览器提供。
createRoot(document.getElementById("root")!).render(<StrictMode><ChatPanel /></StrictMode>);
