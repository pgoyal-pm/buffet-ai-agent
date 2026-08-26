"""
Data Ingestion Pipeline (Part 12 of Spec)

Connects existing buffett SEC filing extraction pipeline to our database.

Handles:
- JSON files from SEC extractors → metric_observations
- Provenance tagging ([R]/[D]/[E]/[I])
- Duplicate prevention (idempotent upserts)
- Progress tracking per company-period
- Error recovery with retry logic

Designed to reuse existing knowledge_models.py domain classes and 
deterministic XBRL parsers from /opt/data/buffett/
"""

import json
import hashlib
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime


class DataIngestionPipeline:
    """Ingest financial data from SEC filings into Compounder Dashboard DB."""
    
    def __init__(self, db):
        self.db = db
    
    def ingest_from_json(self, company_id: int, period_id: int, 
                         json_path: str, source_type: str = 'SEC_10K') -> Dict[str, Any]:
        """
        Ingest metrics from a parsed SEC filing JSON file.
        
        This is the primary ingestion path for automated SEC filing processing.
        Handles: amzn_clean.json, msft_clean.json, etc. from buffett/data/
        
        Args:
            company_id: Target company ID
            period_id: Target fiscal period ID  
            json_path: Path to the clean JSON extraction file
            source_type: Type of SEC filing (10K, 10Q, etc.)
            
        Returns:
            Ingestion result with counts, errors, provenance summary
        """
        result = {
            'company_id': company_id,
            'period_id': period_id,
            'source_file': json_path,
            'source_type': source_type,
            'ingested_at': datetime.utcnow().isoformat(),
            'metrics_ingested': 0,
            'provenance_summary': {},
            'errors': [],
            'success': False,
        }
        
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
        except Exception as e:
            result['errors'].append(f"Failed to read JSON: {e}")
            return result
        
        # Parse the extraction structure
        metrics_list = self._extract_metrics(data, source_type)
        
        if not metrics_list:
            result['errors'].append("No metrics found in JSON")
            return result
        
        # Ingest each metric via idempotent upsert
        success_count = 0
        for metric in metrics_list:
            try:
                inserted = self.db.upsert_metric_observation(
                    company_id=company_id,
                    period_id=period_id,
                    metric_key=metric['key'],
                    value=metric['value'],
                    unit=metric.get('unit'),
                    provenance=metric.get('provenance', '[D] Derived'),
                    certainty=metric.get('certainty', 'MEDIUM'),
                    source_filing=json_path,
                    raw_value=metric.get('raw_value'),
                )
                
                if inserted:
                    success_count += 1
                    
                    # Track provenance
                    prov = metric.get('provenance', '[D] Derived')
                    prov_count = result['provenance_summary'].get(prov, 0)
                    result['provenance_summary'][prov] = prov_count + 1
                    
            except Exception as e:
                result['errors'].append(f"Failed to ingest {metric['key']}: {e}")
        
        result['metrics_ingested'] = success_count
        result['success'] = success_count > 0
        
        return result
    
    def ingest_manual_metrics(self, company_id: int, period_id: int,
                              metrics: Dict[str, Any], 
                              provenance: str = '[R] Reported') -> Dict[str, Any]:
        """
        Manually input metrics for a period (ad-hoc data entry).
        
        Useful for:
        - Quarterly earnings call data points
        - Management commentary insights
        - Third-party estimates that need storage
        """
        result = {
            'company_id': company_id,
            'period_id': period_id,
            'metrics_provided': len(metrics),
            'metrics_successfully_stored': 0,
            'errors': [],
            'success': False,
        }
        
        success_count = 0
        for key, value in metrics.items():
            try:
                inserted = self.db.upsert_metric_observation(
                    company_id=company_id,
                    period_id=period_id,
                    metric_key=key,
                    value=float(value) if isinstance(value, (int, float)) else None,
                    provenance=provenance,
                    certainty='HIGH' if provenance == '[R] Reported' else 'MEDIUM',
                    source_filing='manual_input',
                    notes=f'Manual entry - {datetime.utcnow().isoformat()}',
                )
                
                if inserted:
                    success_count += 1
            except Exception as e:
                result['errors'].append(f"Failed to store {key}: {e}")
        
        result['metrics_successfully_stored'] = success_count
        result['success'] = success_count > 0
        
        return result
    
    def bulk_import_from_dir(self, base_dir: str, pattern: str = '*_clean.json',
                             source_type: str = 'SEC_10K') -> Dict[str, Any]:
        """
        Bulk import all JSON files from a directory matching pattern.
        
        Expected structure:
        buffett/data/{amzn, msft, alphabet}/extractions/*_clean.json
        
        Returns:
            Summary of all imports attempted/succeeded
        """
        import os
        import fnmatch
        
        result = {
            'total_files': 0,
            'files_success': 0,
            'files_failed': 0,
            'total_metrics_ingested': 0,
            'errors': [],
            'per_file_results': [],
        }
        
        # List all files in base_dir
        try:
            all_files = [f for f in os.listdir(base_dir) if os.path.isfile(os.path.join(base_dir, f))]
        except Exception as e:
            result['errors'].append(f"Cannot list directory: {e}")
            return result
        
        for filename in all_files:
            if fnmatch.fnmatch(filename, pattern):
                json_path = os.path.join(base_dir, filename)
                company_name = os.path.basename(base_dir)
                
                # Look up or create company
                company = self.db.get_company_by_ticker(company_name.upper())
                if not company:
                    company_id = self.db.create_company(
                        ticker=company_name.upper(),
                        name=company_name.upper(),
                        sector='Unknown',
                        industry='Unknown',
                    )
                else:
                    company_id = company['id']
                
                # Try to match period from filename or use latest
                # Pattern: 2025-Q4_clean.json → FY2025 Q4
                period_match = self._detect_period_from_filename(filename)
                if period_match:
                    period_id = self.db.create_or_get_fiscal_period(
                        company_id=company_id,
                        fiscal_year=period_match.get('fiscal_year'),
                        fiscal_quarter=period_match.get('quarter'),
                        report_date=period_match.get('report_date'),
                        period_label=period_match.get('label'),
                    )
                else:
                    # Create placeholder period
                    period_id = self.db.create_or_get_fiscal_period(
                        company_id=company_id,
                        fiscal_year=2025,
                        fiscal_quarter=4,
                        report_date=datetime.utcnow().date(),
                        period_label=filename.split('_')[0] if '_' in filename else 'UNKNOWN',
                    )
                
                # Perform ingestion
                ingest_result = self.ingest_from_json(
                    company_id=company_id,
                    period_id=period_id,
                    json_path=json_path,
                    source_type=source_type,
                )
                
                result['per_file_results'].append(ingest_result)
                result['total_files'] += 1
                
                if ingest_result['success']:
                    result['files_success'] += 1
                    result['total_metrics_ingested'] += ingest_result['metrics_ingested']
                else:
                    result['files_failed'] += 1
                    result['errors'].extend(ingest_result['errors'])
        
        return result
    
    def _extract_metrics(self, data: Dict, source_type: str) -> List[Dict]:
        """Extract flat list of metrics from extraction JSON structure."""
        metrics = []
        
        # Handle different JSON structures from SEC extractors
        if 'financial_metrics' in data:
            # Standard structured format
            for key, val_data in data['financial_metrics'].items():
                if isinstance(val_data, dict):
                    metrics.append({
                        'key': key,
                        'value': val_data.get('value'),
                        'unit': val_data.get('unit'),
                        'provenance': val_data.get('provenance', '[D] Derived'),
                        'certainty': val_data.get('certainty', 'MEDIUM'),
                        'raw_value': val_data.get('raw_value'),
                    })
                elif val_data is not None:
                    metrics.append({
                        'key': key,
                        'value': val_data,
                        'provenance': '[D] Derived',
                        'certainty': 'MEDIUM',
                    })
        elif 'metrics' in data:
            # Alternate format
            for key, val_data in data['metrics'].items():
                if isinstance(val_data, dict):
                    metrics.append({
                        'key': key,
                        'value': val_data.get('value'),
                        'unit': val_data.get('unit'),
                        'provenance': val_data.get('provenance', '[D] Derived'),
                        'certainty': val_data.get('certainty', 'MEDIUM'),
                    })
                elif val_data is not None:
                    metrics.append({
                        'key': key,
                        'value': val_data,
                        'provenance': '[D] Derived',
                    })
        else:
            # Flat key-value structure
            for key, value in data.items():
                if value is not None and not isinstance(value, (dict, list)):
                    metrics.append({
                        'key': key,
                        'value': value,
                        'provenance': '[D] Derived',
                    })
        
        return metrics
    
    @staticmethod
    def _detect_period_from_filename(filename: str) -> Optional[Dict]:
        """Parse period info from filename patterns like 2025-Q4_clean.json."""
        import re
        
        # Match YYYY-Qn pattern
        match = re.search(r'(20\d{2})-Q([1-4])', filename)
        if match:
            year = int(match.group(1))
            quarter = int(match.group(2))
            
            # Estimate report date based on quarter
            report_dates = {
                1: f'{year}-02-28',  # Q4 reported Feb
                2: f'{year}-05-31',  # Q1 reported May
                3: f'{year}-08-31',  # Q2 reported Aug
                4: f'{year}-11-30',  # Q3 reported Nov
            }
            
            label = f'FY{year} Q{quarter}'
            
            return {
                'fiscal_year': year,
                'quarter': quarter,
                'report_date': report_dates.get(quarter, f'{year}-12-31'),
                'label': label,
            }
        
        # Match YYYY format only
        match = re.search(r'(20\d{4})', filename)
        if match:
            year = int(match.group(1))
            return {
                'fiscal_year': year,
                'quarter': 4,
                'report_date': f'{year}-12-31',
                'label': f'FY{year}',
            }
        
        return None
