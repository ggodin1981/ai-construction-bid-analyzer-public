export interface BidInfo {
  projectName: string;
  tradeScope: string;
  location: string;
  deadline: string;
  materials: string;
  exclusions: string;
}

export interface DocumentProfile {
  filename: string;
  pageCount: number;
  extractedCharacters: number;
  averageCharsPerPage: number;
  blankPages: number;
  lowTextPages: number;
  detectedDocumentType: string;
  extractionMode: string;
  ocrRecommendation: string;
  ocrAttempted: boolean;
  ocrUsed: boolean;
  ocrStatus: string;
  ocrEngine: string;
  cvPreprocessingApplied: boolean;
  cvPipelineStatus: string;
  cvVisualClassification: string;
  cvAverageSkewAngle: number;
  cvAverageEdgeDensity: number;
  cvAverageInkRatio: number;
  pipelineObservations: string[];
}

export interface BidAnalysis {
  summary: string;
  bidInfo: BidInfo;
  riskItems: string[];
  reviewFlags: string[];
  recommendedActions: string[];
  requiredClarifications: string[];
  bidRecommendation: string;
  recommendationRationale: string;
  commercialQualifications: string[];
  estimatorReviewMemo: string;
  sourceEvidence: string[];
  reviewBasis: string;
  readinessScore: number;
  readinessLabel: 'Ready' | 'Needs Review' | 'High Risk' | string;
  documentText: string;
  aiProvider: string;
  documentProfile: DocumentProfile;
  analysisGeneratedAt: string;
}
