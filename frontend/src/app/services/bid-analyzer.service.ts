import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { BidAnalysis } from '../models/bid-analysis.model';

const runtimeHost = window.location.hostname || 'localhost';
const isLocalHost = ['localhost', '127.0.0.1'].includes(runtimeHost);
const DEFAULT_PRODUCTION_API_URL = 'https://ai-construction-bid-analyzer.up.railway.app';
const API_BASE_URL =
  (window as any).__env?.apiUrl ||
  (isLocalHost
    ? `${window.location.protocol}//${runtimeHost}:8000`
    : DEFAULT_PRODUCTION_API_URL);

export interface AskDocumentResponse {
  answer: string;
  confidence: 'High' | 'Medium' | 'Low' | string;
  evidence: string[];
  limitations: string;
}

@Injectable({ providedIn: 'root' })
export class BidAnalyzerService {
  constructor(private readonly http: HttpClient) {}

  analyze(file: File): Observable<BidAnalysis> {
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<BidAnalysis>(`${API_BASE_URL}/api/analyze`, formData);
  }

  ask(question: string, documentText: string): Observable<AskDocumentResponse> {
    return this.http.post<AskDocumentResponse>(`${API_BASE_URL}/api/ask`, {
      question,
      document_text: documentText
    });
  }
}
