import { CommonModule } from '@angular/common';
import { Component, OnDestroy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { BidAnalysis } from './models/bid-analysis.model';
import { AskDocumentResponse, BidAnalyzerService } from './services/bid-analyzer.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css'
})
export class AppComponent implements OnDestroy {
  selectedFile?: File;
  analysis?: BidAnalysis;
  pdfPreviewUrl?: SafeResourceUrl;
  question = '';
  answer?: AskDocumentResponse;
  loading = false;
  asking = false;
  error = '';
  private pdfPreviewObjectUrl?: string;
  private reviewPrintFrame?: HTMLIFrameElement;
  readonly operationalSignals = [
    {
      label: 'Bid Qualification',
      value: 'Scope, exclusions, schedule, and submission checks'
    },
    {
      label: 'Decision Support',
      value: 'Go / no-go / escalate guidance with commercial context'
    },
    {
      label: 'Preconstruction Workflow',
      value: 'Upload, triage, clarify, and hand off faster'
    }
  ];
  readonly reviewChecklist = [
    'Confirm scope ownership and package boundaries.',
    'Validate bid date, notice-to-proceed window, and sequencing pressure.',
    'Surface exclusions, fee gaps, and field-verification obligations.',
    'Capture clarifications before final pricing handoff.'
  ];
  readonly planAnalyzerCapabilities = [
    'Extracts project, trade scope, deadline, materials, and exclusions from plan/spec text.',
    'Highlights schedule pressure, incomplete design, field verification, and commercial risk signals.',
    'Builds evidence-backed review notes for estimator handoff and bid qualification.'
  ];
  readonly engineeringFitSignals = [
    {
      title: 'Python Analytics Core',
      detail: 'PDF extraction, structured review output, and LLM orchestration live in a production-style FastAPI service.'
    },
    {
      title: 'Angular Operator Workspace',
      detail: 'Typed models and service contracts keep the estimator-facing review flow clean, fast, and easy to extend.'
    },
    {
      title: '.NET / Azure Ready Shape',
      detail: 'The analytics layer is separated behind API boundaries so it can slot into a larger queue or platform workflow.'
    }
  ];
  readonly defaultQuestions = [
    'What scope items create the biggest pricing exposure?',
    'Are there any exclusions or assumptions that need clarification?',
    'What schedule language could impact labor planning?'
  ];

  constructor(
    private readonly bidAnalyzer: BidAnalyzerService,
    private readonly sanitizer: DomSanitizer
  ) {}

  ngOnDestroy(): void {
    this.revokePdfPreviewUrl();
    this.cleanupReviewPrintFrame();
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile = input.files?.[0];
    this.error = '';
    this.analysis = undefined;
    this.answer = undefined;
    this.question = '';
    this.setPdfPreview(this.selectedFile);
  }

  analyzeDocument(): void {
    if (!this.selectedFile) {
      this.error = 'Please select a construction bid PDF first.';
      return;
    }

    this.loading = true;
    this.error = '';
    this.answer = undefined;

    this.bidAnalyzer.analyze(this.selectedFile).subscribe({
      next: (result) => {
        this.analysis = result;
        this.loading = false;
        this.question = '';
      },
      error: (err) => {
        const apiDetail =
          err?.error?.detail ||
          err?.error?.message ||
          err?.message ||
          err?.statusText;
        this.error = apiDetail
          ? `Unable to analyze document: ${apiDetail}`
          : 'Unable to analyze document. Please check the API service.';
        this.loading = false;
      }
    });
  }

  askQuestion(): void {
    if (!this.question.trim() || !this.analysis?.documentText) return;

    this.asking = true;
    this.answer = undefined;

    this.bidAnalyzer.ask(this.question, this.analysis.documentText).subscribe({
      next: (result) => {
        this.answer = result;
        this.asking = false;
      },
      error: () => {
        this.answer = {
          answer: 'A live document answer could not be generated right now.',
          confidence: 'Low',
          evidence: [],
          limitations: 'The AI answer service was unavailable during this request.'
        };
        this.asking = false;
      }
    });
  }

  setQuestion(question: string): void {
    this.question = question;
  }

  downloadImportedPdf(): void {
    if (!this.selectedFile || !this.pdfPreviewObjectUrl) return;
    this.triggerDownload(this.pdfPreviewObjectUrl, this.selectedFile.name);
  }

  downloadReviewPacket(): void {
    if (!this.analysis) return;
    const html = this.buildReviewPacketHtml(false);
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const filename = `${this.getDocumentStem()}-review-copy.html`;
    this.triggerBlobDownload(blob, filename);
  }

  printReviewPacket(): void {
    if (!this.analysis) return;
    this.error = '';
    this.cleanupReviewPrintFrame();

    const reviewHtml = this.buildReviewPacketHtml(false);
    const iframe = document.createElement('iframe');
    iframe.style.position = 'fixed';
    iframe.style.right = '0';
    iframe.style.bottom = '0';
    iframe.style.width = '0';
    iframe.style.height = '0';
    iframe.style.border = '0';
    iframe.style.visibility = 'hidden';

    iframe.onload = () => {
      const frameWindow = iframe.contentWindow;
      if (!frameWindow) {
        this.error = 'The print review frame could not be prepared.';
        this.cleanupReviewPrintFrame();
        return;
      }

      const cleanup = () => {
        window.setTimeout(() => this.cleanupReviewPrintFrame(), 300);
      };

      frameWindow.onafterprint = cleanup;
      frameWindow.focus();
      window.setTimeout(() => {
        frameWindow.print();
      }, 150);
    };

    document.body.appendChild(iframe);
    this.reviewPrintFrame = iframe;

    const frameDocument = iframe.contentDocument;
    if (!frameDocument) {
      this.error = 'The print review frame could not be initialized.';
      this.cleanupReviewPrintFrame();
      return;
    }

    frameDocument.open();
    frameDocument.write(reviewHtml);
    frameDocument.close();
  }

  downloadAnalysisJson(): void {
    if (!this.analysis) return;

    const exportPayload = {
      exportedAt: new Date().toISOString(),
      sourceFile: this.selectedFile?.name ?? this.analysis.documentProfile.filename,
      analysis: this.analysis,
      latestQuestion: this.question || null,
      latestAnswer: this.answer ?? null,
    };
    const blob = new Blob([JSON.stringify(exportPayload, null, 2)], {
      type: 'application/json;charset=utf-8',
    });
    const filename = `${this.getDocumentStem()}-analysis.json`;
    this.triggerBlobDownload(blob, filename);
  }

  get scoreClass(): string {
    const score = this.analysis?.readinessScore ?? 0;
    if (score >= 80) return 'score-ready';
    if (score >= 55) return 'score-review';
    return 'score-risk';
  }

  get scoreTone(): string {
    const score = this.analysis?.readinessScore ?? 0;
    if (score >= 80) return 'Bid can move toward pricing with limited blockers.';
    if (score >= 55) return 'Commercial review is advised before final submission.';
    return 'Material uncertainty remains across scope, assumptions, or schedule.';
  }

  get recommendationClass(): string {
    const recommendation = this.analysis?.bidRecommendation?.toLowerCase() ?? '';
    if (recommendation === 'bid') return 'recommendation-bid';
    if (recommendation === 'no bid') return 'recommendation-no-bid';
    return 'recommendation-escalate';
  }

  get generatedAtLabel(): string {
    if (!this.analysis?.analysisGeneratedAt) return '';
    return new Date(this.analysis.analysisGeneratedAt).toLocaleString();
  }

  get selectedFileSizeLabel(): string {
    if (!this.selectedFile) return '';
    const size = this.selectedFile.size;
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(2)} MB`;
  }

  private setPdfPreview(file?: File): void {
    this.revokePdfPreviewUrl();
    if (!file) {
      this.pdfPreviewUrl = undefined;
      return;
    }

    this.pdfPreviewObjectUrl = URL.createObjectURL(file);
    this.pdfPreviewUrl = this.sanitizer.bypassSecurityTrustResourceUrl(this.pdfPreviewObjectUrl);
  }

  private revokePdfPreviewUrl(): void {
    if (this.pdfPreviewObjectUrl) {
      URL.revokeObjectURL(this.pdfPreviewObjectUrl);
      this.pdfPreviewObjectUrl = undefined;
    }
  }

  private triggerBlobDownload(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    this.triggerDownload(url, filename);
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private triggerDownload(url: string, filename: string): void {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.rel = 'noopener';
    link.click();
  }

  private getDocumentStem(): string {
    const filename = this.selectedFile?.name || this.analysis?.documentProfile.filename || 'document-review';
    return filename.replace(/\.pdf$/i, '').replace(/[^a-z0-9-_]+/gi, '-').replace(/^-+|-+$/g, '') || 'document-review';
  }

  private cleanupReviewPrintFrame(): void {
    if (!this.reviewPrintFrame) return;
    this.reviewPrintFrame.remove();
    this.reviewPrintFrame = undefined;
  }

  private buildReviewPacketHtml(autoPrint: boolean): string {
    if (!this.analysis) return '';

    const answerSection = this.answer
      ? `
        <section>
          <h2>Document Interrogation</h2>
          <div class="callout">
            <strong>Latest Analyst Response</strong>
            <p>${this.escapeHtml(this.answer.answer)}</p>
            <p><strong>Confidence:</strong> ${this.escapeHtml(this.answer.confidence)}</p>
            ${this.buildListSection('Supporting Evidence', this.answer.evidence)}
            <p><strong>Limitations:</strong> ${this.escapeHtml(this.answer.limitations)}</p>
          </div>
        </section>
      `
      : '';

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>${this.escapeHtml(this.getDocumentStem())} Review Copy</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #162126;
      --muted: #5a6a70;
      --line: #d9dfdc;
      --panel: #f6f4ed;
      --panel-strong: #eef4e4;
      --accent: #9a6700;
      --good: #166534;
      --warn: #9a6700;
      --risk: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background: #ffffff;
      line-height: 1.55;
    }
    main {
      max-width: 960px;
      margin: 0 auto;
      padding: 36px 32px 56px;
    }
    h1, h2, h3 { margin: 0; }
    h1 {
      font-size: 30px;
      line-height: 1.1;
      margin-top: 10px;
    }
    h2 {
      font-size: 18px;
      margin-bottom: 14px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--line);
    }
    p { margin: 0; }
    section { margin-top: 26px; }
    .eyebrow {
      color: var(--accent);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }
    .lede {
      margin-top: 16px;
      color: var(--muted);
    }
    .summary {
      margin-top: 18px;
      padding: 18px 20px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
    }
    .decision {
      display: inline-block;
      margin-top: 14px;
      padding: 8px 12px;
      border-radius: 999px;
      font-weight: 700;
      background: ${this.getReviewDecisionColor()};
      color: ${this.getReviewDecisionTextColor()};
    }
    .meta-grid, .profile-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .meta-card {
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
    }
    .meta-card span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .meta-card strong {
      display: block;
      margin-top: 6px;
      font-size: 15px;
    }
    ul {
      margin: 12px 0 0;
      padding-left: 20px;
    }
    li { margin-top: 6px; }
    .callout {
      padding: 16px 18px;
      border-left: 4px solid #90b449;
      border-radius: 14px;
      background: var(--panel-strong);
    }
    .source-note {
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .footer {
      margin-top: 28px;
      color: var(--muted);
      font-size: 12px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    @media print {
      body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
      main { padding: 20px 18px 28px; }
    }
  </style>
</head>
<body${autoPrint ? ' onload="window.print()"' : ''}>
  <main>
    <p class="eyebrow">Construction Bid Review Packet</p>
    <h1>${this.escapeHtml(this.analysis.documentProfile.filename)}</h1>
    <p class="lede">Print-ready analyst summary for estimator review, qualification discussion, and proposal handoff.</p>

    <section class="summary">
      <h2>Executive Summary</h2>
      <p>${this.escapeHtml(this.analysis.summary)}</p>
      <div class="decision">${this.escapeHtml(this.analysis.bidRecommendation)}</div>
      <p class="source-note">${this.escapeHtml(this.analysis.recommendationRationale)}</p>
      <p class="source-note">Generated ${this.escapeHtml(this.generatedAtLabel || new Date().toLocaleString())}</p>
    </section>

    <section>
      <h2>Review Snapshot</h2>
      <div class="meta-grid">
        <div class="meta-card"><span>Readiness Score</span><strong>${this.escapeHtml(String(this.analysis.readinessScore))}% - ${this.escapeHtml(this.analysis.readinessLabel)}</strong></div>
        <div class="meta-card"><span>Project</span><strong>${this.escapeHtml(this.analysis.bidInfo.projectName)}</strong></div>
        <div class="meta-card"><span>Trade Scope</span><strong>${this.escapeHtml(this.analysis.bidInfo.tradeScope)}</strong></div>
        <div class="meta-card"><span>Deadline</span><strong>${this.escapeHtml(this.analysis.bidInfo.deadline)}</strong></div>
        <div class="meta-card"><span>Location</span><strong>${this.escapeHtml(this.analysis.bidInfo.location)}</strong></div>
        <div class="meta-card"><span>Exclusions</span><strong>${this.escapeHtml(this.analysis.bidInfo.exclusions)}</strong></div>
      </div>
    </section>

    <section>
      <h2>Document Analytics Profile</h2>
      <div class="profile-grid">
        <div class="meta-card"><span>Detected Type</span><strong>${this.escapeHtml(this.analysis.documentProfile.detectedDocumentType)}</strong></div>
        <div class="meta-card"><span>Extraction Mode</span><strong>${this.escapeHtml(this.analysis.documentProfile.extractionMode)}</strong></div>
        <div class="meta-card"><span>Pages Reviewed</span><strong>${this.escapeHtml(String(this.analysis.documentProfile.pageCount))}</strong></div>
        <div class="meta-card"><span>Characters Extracted</span><strong>${this.escapeHtml(String(this.analysis.documentProfile.extractedCharacters))}</strong></div>
      </div>
      <p class="source-note">${this.escapeHtml(this.analysis.documentProfile.ocrRecommendation)}</p>
      ${this.buildListSection('Pipeline Observations', this.analysis.documentProfile.pipelineObservations)}
    </section>

    <section>
      <h2>Priority Review Output</h2>
      ${this.buildListSection('Risk Register', this.analysis.riskItems)}
      ${this.buildListSection('Review Flags', this.analysis.reviewFlags)}
      ${this.buildListSection('Recommended Actions', this.analysis.recommendedActions)}
      ${this.buildListSection('Required Clarifications', this.analysis.requiredClarifications)}
      ${this.buildListSection('Commercial Qualifications', this.analysis.commercialQualifications)}
    </section>

    <section>
      <h2>Estimator Review Memo</h2>
      <div class="callout">
        <p>${this.escapeHtml(this.analysis.estimatorReviewMemo)}</p>
      </div>
      <p class="source-note">${this.escapeHtml(this.analysis.reviewBasis)}</p>
    </section>

    <section>
      <h2>Supporting Evidence</h2>
      ${this.buildListSection('Evidence Captured', this.analysis.sourceEvidence)}
    </section>

    ${answerSection}

    <p class="footer">Prepared by the Bid Qualification Workspace for internal review use.</p>
  </main>
</body>
</html>`;
  }

  private buildListSection(title: string, items: string[]): string {
    if (!items?.length) return '';
    const listItems = items.map((item) => `<li>${this.escapeHtml(item)}</li>`).join('');
    return `
      <div style="margin-top: 14px;">
        <strong>${this.escapeHtml(title)}</strong>
        <ul>${listItems}</ul>
      </div>
    `;
  }

  private getReviewDecisionColor(): string {
    const recommendation = this.analysis?.bidRecommendation?.toLowerCase() ?? '';
    if (recommendation === 'bid') return '#e9f9ee';
    if (recommendation === 'no bid') return '#fdecec';
    return '#fff4d6';
  }

  private getReviewDecisionTextColor(): string {
    const recommendation = this.analysis?.bidRecommendation?.toLowerCase() ?? '';
    if (recommendation === 'bid') return '#166534';
    if (recommendation === 'no bid') return '#b42318';
    return '#9a6700';
  }

  private escapeHtml(value: string): string {
    return value
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }
}
