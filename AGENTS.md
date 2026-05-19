# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## Project Overview

**academic-paper-search** — 学术论文自动化搜索工具（P1 优先级）。

做一个 skill/tool，帮助自动化论文查找工作流。

## Workflow

1. 通过 OpenAlex、DBLP、arXiv 组合搜索符合条件的论文条目
2. 支持按关键词/作者/机构搜索
3. 爬取论文元数据，下载 PDF 到本地
4. 去重合并，生成统一格式的报告

## Key Technical Points

- **多平台 API 调用**：OpenAlex、DBLP、arXiv（可能扩展 IEEE、ACM）
- **Cookie 管理和会话维持**：处理登录态和反爬
- **PDF 下载和存储**：本地化论文 PDF
- **去重算法**：跨平台结果合并去重

## Tags

`#skill` `#学术工具` `#论文爬虫` `#OpenAlex` `#DBLP` `#arXiv` `#IEEE` `#ACM`

## Status

🆕 待规划 — 初步阶段，需要拆解任务并开始实现。

## Build & Test Commands

<!-- Add build and test commands as the project evolves -->

## Code Style

<!-- Document code style conventions -->

## Architecture

<!-- Document architectural decisions and patterns -->
