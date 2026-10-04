"""Numerical checks of B25 source formulas. Extended as reading progresses."""
import math,json
from pathlib import Path

def integrate(fn,lo=-9,hi=9,n=12000):
 h=(hi-lo)/n
 return h/3*(fn(lo)+fn(hi)+sum((4 if i%2 else 2)*fn(lo+i*h) for i in range(1,n)))

def phi(x):return math.exp(-x*x/2)/math.sqrt(2*math.pi)
def cdf(x):return .5*math.erfc(-x/math.sqrt(2))
def ordermean(k,n):
 coef=math.factorial(n)/(math.factorial(k-1)*math.factorial(n-k))
 return integrate(lambda x:x*coef*cdf(x)**(k-1)*cdf(-x)**(n-k)*phi(x))

out={'henery_equal_ability':{}}
for n in [3,4,8,11,14]:
 a,b,c=[ordermean(k,n) for k in [1,2,3]]
 lam=(n-1)/n*(b+(a+b)/(n-2))/a
 tau=(n-1)/n*(c+(a+b+c)/(n-3))/a if n>=4 else None
 out['henery_equal_ability'][n]={'order_means':[a,b,c],'lambda':lam,'tau':tau}
assert abs(out['henery_equal_ability'][4]['tau']-.5336)<.00005
assert abs(out['henery_equal_ability'][3]['lambda']-2/3)<1e-9
out['henery_printed_win_formula_counterexample']={
 'theta_i_minus1_theta_other0_printed_max_probability': cdf(-1/math.sqrt(2)),
 'correct_min_probability':cdf(1/math.sqrt(2))}
out['ch5_log_distance']={'double_distance_delta_kmh':-5.583*math.log(2),
 'ten_percent_distance_delta_kmh':-5.583*math.log(1.1)}
out['ch5_f_printed_inputs']={'ability':((1506-970)/493)/(970/2155),
 'distance_slope':((970-694)/493)/(694/1664)}
out['ch5_f_sd_exact']=math.sqrt(2*1664**2*(493+1664-2)/(493*(1664-2)**2*(1664-4)))
b=50*(math.sqrt(3)-1)
out['ch6_optimal_pool_bet']={'bet':b,'final_market_share':(50+b)/(100+b),'objective_probability':.75}
w=lambda p:math.exp(-(-math.log(p))**.8)
out['ch6_prelec_p75']={'w_p':w(.75),'w_complement':w(.25),'weight_sum':w(.75)+w(.25),'normalized_m':w(.75)/(w(.75)+w(.25))}
rho,take=.25,.2
out['ch8_takeout']={'correct_net_odds':(1-take)/rho-1,'printed_net_odds':1/(rho*(1-take))-1}
F=X=100;v=.2;T=.25
d1=(math.log(F/X)+.5*v*v*T)/(v*math.sqrt(T));d2=d1-v*math.sqrt(T)
out['ch9_black_put']={'correct':X*cdf(-d2)-F*cdf(-d1),'printed':X*cdf(-d1)-F*cdf(-d2)}
out['ch9_selected_t_checks']=[]
for m,s,n in [(.9792,.4949,187),(.9883,.6677,187),(.9989,.7746,187),(.7926,2.067,123),(.7926,2.067,2460),(.0123,.1345,39)]:
 out['ch9_selected_t_checks'].append({'mean':m,'sd':s,'n':n,'t_null1':(m-1)/(s/math.sqrt(n)),'t_null1_00583':(m-1.00583)/(s/math.sqrt(n))})
assert out['ch9_black_put']['correct']>0>out['ch9_black_put']['printed']
out['ch11_brownian_checks']=[{'spread':p,'elapsed':t,'lead':l,'p_win':cdf((l+(1-t)*p)/(11.6*math.sqrt(1-t)))} for p,t,l in [(5,.75,0),(5,.75,2),(10,.90,10)]]
def binom_upper(k,n,p):
 return sum(math.exp(math.lgamma(n+1)-math.lgamma(j+1)-math.lgamma(n-j+1)+j*math.log(p)+(n-j)*math.log1p(-p)) for j in range(k,n+1))
out['ch12_binomial_profit_tests']=[{'wins':k,'settled_nonpush':n,'win_rate':k/n,'null_break_even':11/21,'one_sided_p_value':binom_upper(k,n,11/21),'p_value_vs_half':binom_upper(k,n,.5)} for k,n in [(133,245),(255,457),(56,96)]]
# Chapter 17: small-edge expansion and the stated seven-outcome portfolio.
edge=.001
p=(1+edge)/2
growth=p*math.log1p(edge)+(1-p)*math.log1p(-edge)
out['ch17_growth_factor']={'distribution':'X=0 or 2, b=1, P(X=2)=(1+epsilon)/2','epsilon':edge,'exact_optimal_u':edge,'exact_growth':growth,'printed_approx':edge**2/(1-edge**2),'correct_leading_approx':edge**2/(2*(1-edge**2))}
assert abs(growth/(edge**2)-.5)<1e-6
probs=[.25,.05,.20,.05,.20,.15,.10]
asks=[14,4,19,5,21,27,17];bids=[12,2,17,3,19,25,15]
out['ch17_portfolios']=[]
for buys,sells in [([0,1],[5,6]),([0,1],[4,5,6])]:
 idle=[i for i in range(7) if i not in buys+sells]
 t=sum(probs[i] for i in idle)/(1-sum(asks[i]/100 for i in buys)-sum(bids[i]/100 for i in sells))
 u=[probs[i]-t*asks[i]/100 if i in buys else 0 for i in range(7)]
 v=[(100-bids[i])*(t*bids[i]/100-probs[i])/bids[i] if i in sells else 0 for i in range(7)]
 wealth=[1-sum(u)+100*u[i]/asks[i]+sum(v[j]*bids[j]/(100-bids[j]) for j in range(7))-100*v[i]/(100-bids[i]) for i in range(7)]
 failures=[i+1 for i in idle if not 100*probs[i]/asks[i]<=t<=100*probs[i]/bids[i]]
 out['ch17_portfolios'].append({'buy_horses':[i+1 for i in buys],'sell_horses':[i+1 for i in sells],'t':t,'buy_fractions':u,'sell_fractions':v,'state_wealth_ratio':wealth,'growth':sum(probs[i]*math.log(wealth[i]) for i in range(7)),'inactive_kkt_violations':failures})
assert out['ch17_portfolios'][0]['inactive_kkt_violations']==[5]
assert out['ch17_portfolios'][1]['inactive_kkt_violations']==[]
# Chapters 22–24: lottery probability, units, prize sharing, and elasticity.
lottery_p=1/math.comb(49,6)
out['ch22_lottery']={'combinations':math.comb(49,6),'p':lottery_p,'rollover_20m':math.exp(20_000_000*math.log1p(-lottery_p)),'rollover_40m':math.exp(40_000_000*math.log1p(-lottery_p)),'sales_effect_mean_25pct':1.25**.442-1,'sales_effect_skew_10pct':1.1**.160-1}
out['ch23_keno10']={}
for state,payouts in {'Georgia':{10:100000,9:5000,8:500,7:50,6:10,5:2,0:5},'Kansas':{10:100000,9:2000,8:500,7:50,6:10,5:1,0:5},'Massachusetts':{10:100000,9:10000,8:500,7:80,6:20,5:2,0:2},'Washington':{10:100000,9:5000,8:500,7:50,6:5,5:2,0:3}}.items():
 out['ch23_keno10'][state]=sum(math.comb(20,k)*math.comb(60,10-k)/math.comb(80,10)*v for k,v in payouts.items())
assert round(out['ch23_keno10']['Georgia']*100,1)==63.7
assert round(out['ch23_keno10']['Washington']*100,1)==48.8
out['ch24_shared_prize_identity']=[]
for other_tickets in (0,1,10,100):
 chance=.01
 direct=chance*sum(math.comb(other_tickets,k)*chance**k*(1-chance)**(other_tickets-k)/(k+1) for k in range(other_tickets+1))
 exact=-math.expm1((other_tickets+1)*math.log1p(-chance))/(other_tickets+1)
 approx=-math.expm1(-other_tickets*chance)/other_tickets if other_tickets else chance
 assert abs(direct-exact)<1e-14
 out['ch24_shared_prize_identity'].append({'other_tickets':other_tickets,'probability':chance,'direct_expected_share':direct,'exact_identity':exact,'poisson_approx':approx})
path=Path(__file__).parent/'reading_v2/B25-numerical-audit.json'
path.write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
