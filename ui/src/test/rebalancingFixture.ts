import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { existsSync } from "node:fs";
import type { RebalancingResult } from "../types";

// Synthetic test data calculated by the actual Python simulator, never a demo market series.
export function rebalancingFixture(): RebalancingResult {
  const source = `
import json,sys,hashlib
import numpy as np
import pandas as pd
from app.analytics.models import ReturnPanel
from app.analytics.rebalancing import analyze_rebalancing
from app.schemas.frontier import RebalancingOptimizationRequest
sys.stdout.reconfigure(encoding='utf-8')
rng=np.random.default_rng(19)
returns=pd.DataFrame(rng.normal([.006,.003,.004],[.035,.015,.025],size=(120,3)),index=pd.period_range('2000-01',periods=120,freq='M'),columns=['1','175','243'])
body=RebalancingOptimizationRequest(instrument_ids=[1,175,243],user_weights={'1':.6,'175':.2,'243':.2},risk_free_source='fixed_annual',transaction_fee_rate=.001)
metadata=[]
for asset in returns.columns:
 digest=hashlib.sha256(returns[asset].to_numpy(dtype='<f8').tobytes()).hexdigest()
 metadata.append(dict(instrument_id=int(asset),series_id=int(asset),source='synthetic_test_fixture',currency='USD',series_version=digest,series_sha256=digest,last_updated_at='2009-12-31',observation_count=120,ticker='SYNTHETIC_'+asset,groups=[],start_date='2000-01-31',end_date='2009-12-31'))
panel=ReturnPanel(returns,list(returns.columns),metadata=metadata,warnings=['Synthetic deterministic test data, not a market return series.'])
result=analyze_rebalancing(panel,user_weights=body.user_weights,risk_free_rate=body.risk_free_rate,**body.estimator_options())
result['reproducibility']['api_request']=body.model_dump(mode='json')
result['reproducibility']['random_seed']=body.random_seed
result['reproducibility']['optimization_parameters']={'user_weights':body.user_weights,'transaction_fee_rate':body.transaction_fee_rate}
print(json.dumps(result))
`;
  const executable =
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python";
  const python = existsSync(resolve("..", executable))
    ? resolve("..", executable)
    : process.env.QCROSS_PYTHON ||
      (process.platform === "win32" ? "python" : "python3");
  return JSON.parse(
    execFileSync(python, ["-c", source], {
      cwd: resolve(".."),
      encoding: "utf8",
      maxBuffer: 4 * 1024 * 1024,
    }),
  );
}
