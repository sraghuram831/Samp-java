//+------------------------------------------------------------------+
//|                                                  SweepOB_EA.mq5  |
//|   "Sweep setup" (video 1): 1H Order Block POI -> 1M sweep ->     |
//|   1M CHoCH (MSS) -> 1M Order Block entry. LuxAlgo-style SMC.     |
//|                                                                  |
//|   1. H1 structure (CHoCH / BOS) sets the trend.                  |
//|   2. The BOS leaves a H1 ORDER BLOCK (the orange zone):          |
//|        bearish BOS -> supply OB above (highest candle before it) |
//|        bullish BOS -> demand OB below (lowest candle before it)  |
//|      Previous-day high/low can be used as extra POIs.            |
//|   3. In a killzone, price trades back into that H1 zone.         |
//|   4. On M1 price sweeps the last minor swing (liquidity), then   |
//|      breaks structure the other way = CHoCH / MSS.               |
//|   5. The M1 order block of that move (grey box) = entry.         |
//|      Limit at the OB edge, SL beyond the OB / sweep wick,        |
//|      TP = fixed RR (the video's examples are about 1:1 to 1:3).   |
//|                                                                  |
//|   SMC logic re-implemented from the published LuxAlgo "Smart     |
//|   Money Concepts" rules. Not LuxAlgo's source code.              |
//+------------------------------------------------------------------+
#property copyright "R"
#property version   "1.00"
#property description "H1 order block + M1 sweep/CHoCH + M1 order block entry, LuxAlgo-style SMC markings"

#include <Trade/Trade.mqh>

enum ENUM_BIAS_MODE { BIAS_HTF_STRUCTURE=0, BIAS_BOTH_SIDES=1 };
enum ENUM_OB_LEVEL  { OB_NEAR_EDGE=0, OB_MIDPOINT=1 };
enum ENUM_LOT_MODE  { LOT_RISK_PERCENT=0, LOT_FIXED=1 };

input group "=== Timeframes ==="
input ENUM_TIMEFRAMES InpHTF          = PERIOD_H1;  // Trend + order block timeframe
input ENUM_TIMEFRAMES InpLTF          = PERIOD_M1;  // Entry timeframe (attach EA to this chart)

input group "=== SMC structure (LuxAlgo-style) ==="
input int    InpHTFLen          = 10;   // HTF structure length
input int    InpLTFLen          = 5;    // LTF internal structure length (CHoCH / MSS)
input int    InpLTFSwingLen     = 50;   // LTF swing structure length (drawing only)
input int    InpMaxHTFOB        = 6;    // Active HTF order blocks kept
input int    InpHistoryBars     = 1500; // LTF bars to mark on start
input int    InpHTFHistoryBars  = 800;  // HTF bars to load

input group "=== Setup rules ==="
input ENUM_BIAS_MODE InpBiasMode   = BIAS_HTF_STRUCTURE; // Bias mode
input bool   InpUseHTFOB           = true;  // POI: HTF order blocks
input bool   InpUsePrevDay         = true;  // POI: previous day high/low
input bool   InpRequireSweep       = true;  // Require sweep of last M1 swing before the CHoCH
input bool   InpRequireCHoCH       = true;  // MSS must be a CHoCH (not BOS)
input int    InpMaxBarsInZone      = 60;    // Max LTF bars from zone touch to MSS

input group "=== Killzones (IST, HH:MM-HH:MM, empty = off) ==="
input string InpKZ1 = "05:30-08:30";   // Killzone 1 (Asia)
input string InpKZ2 = "12:30-14:30";   // Killzone 2 (London)
input string InpKZ3 = "18:30-20:30";   // Killzone 3 (New York)
input int    InpTesterGMTOffsetMin = 120; // Broker server GMT offset in minutes (TESTER only; live = auto)

input group "=== Entry & exits ==="
input ENUM_OB_LEVEL InpOBLevel     = OB_NEAR_EDGE; // Entry level inside the M1 order block
input int    InpLimitExpiryBars    = 30;    // Cancel unfilled limit after N LTF bars
input double InpSLBufferPoints     = 20;    // SL buffer beyond OB / sweep (points)
input double InpMaxSLPoints        = 0;     // Max SL size in points (0 = off)
input double InpRR                 = 3.0;   // Reward : Risk
input double InpBreakEvenR         = 1.0;   // Move SL to entry at +R (0 = off)

input group "=== Risk ==="
input ENUM_LOT_MODE InpLotMode     = LOT_RISK_PERCENT; // Lot mode
input double InpFixedLot           = 0.01;  // Fixed lot
input double InpRiskPercent        = 0.5;   // Risk % of balance per trade
input double InpMaxLot             = 0.02;  // Hard lot cap (0 = off)
input bool   InpAllowMinLot        = false; // Use min lot if risk% lot is smaller
input double InpMaxDailyLossMoney  = 0;     // Stop for the day at this loss (money, 0 = off)
input int    InpMaxTradesPerDay    = 3;     // Max trades per day
input bool   InpOneTradePerKillzone= true;  // Only one setup per killzone
input double InpMaxSpreadPoints    = 0;     // Max spread in points (0 = off)

input group "=== Drawing ==="
input bool   InpDrawLTFStruct      = true;  // M1 internal BOS/CHoCH (dashed)
input bool   InpDrawLTFSwing       = true;  // M1 swing BOS/CHoCH (solid)
input bool   InpDrawHTFStruct      = true;  // H1 BOS/CHoCH
input bool   InpDrawHTFOB          = true;  // H1 order blocks (orange)
input bool   InpDrawKZ             = true;  // Killzone boxes
input bool   InpDrawSetups         = true;  // Sweeps, MSS, M1 OB, entry/SL/TP zones
input bool   InpDrawLevels         = true;  // Previous day high/low
input int    InpMaxObjects         = 800;   // Max structure/setup objects kept
input color  InpBullColor          = C'8,153,129';
input color  InpBearColor          = C'242,54,69';
input color  InpHTFOBColor         = C'255,228,196';
input color  InpLTFOBColor         = C'210,210,210';
input color  InpMSSColor           = clrDarkOrange;
input color  InpSweepColor         = clrDarkViolet;
input color  InpKZ1Color           = C'225,230,255';
input color  InpKZ2Color           = C'255,228,232';
input color  InpKZ3Color           = C'222,245,232';

input group "=== General ==="
input long   InpMagic              = 260111; // Magic number
input string InpComment            = "SweepOB"; // Order comment

CTrade   trade;
string   PFX = "SWOB_";
long     g_objSeq = 0;
string   g_ring[];

//+------------------------------------------------------------------+
//| Drawing helpers                                                  |
//+------------------------------------------------------------------+
string TFName(ENUM_TIMEFRAMES tf) { return StringSubstr(EnumToString(tf), 7); }
string NewName(string tag) { g_objSeq++; return PFX + tag + "_" + IntegerToString(g_objSeq); }

void Track(string nm)
{
   int n = ArraySize(g_ring);
   if(n >= InpMaxObjects && n > 0)
   {
      ObjectDelete(0, g_ring[0]);
      ObjectDelete(0, g_ring[0] + "_t");
      ObjectDelete(0, g_ring[0] + "_b");
      for(int i = 1; i < n; i++) g_ring[i-1] = g_ring[i];
      g_ring[n-1] = nm;
   }
   else
   {
      ArrayResize(g_ring, n + 1, 200);
      g_ring[n] = nm;
   }
}

void SetCommon(string nm)
{
   ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
}

void DrawSeg(string nm, datetime t1, datetime t2, double p, color c, ENUM_LINE_STYLE st, int w)
{
   if(ObjectFind(0, nm) < 0) ObjectCreate(0, nm, OBJ_TREND, 0, t1, p, t2, p);
   else { ObjectMove(0, nm, 0, t1, p); ObjectMove(0, nm, 1, t2, p); }
   ObjectSetInteger(0, nm, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nm, OBJPROP_STYLE, st);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, w);
   ObjectSetInteger(0, nm, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, nm, OBJPROP_RAY_LEFT, false);
   SetCommon(nm);
}

void DrawText(string nm, datetime t, double p, string txt, color c, int fs, ENUM_ANCHOR_POINT a)
{
   if(ObjectFind(0, nm) < 0) ObjectCreate(0, nm, OBJ_TEXT, 0, t, p);
   else ObjectMove(0, nm, 0, t, p);
   ObjectSetString(0, nm, OBJPROP_TEXT, txt);
   ObjectSetString(0, nm, OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, fs);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nm, OBJPROP_ANCHOR, a);
   SetCommon(nm);
}

void DrawRect(string nm, datetime t1, double p1, datetime t2, double p2, color c)
{
   if(ObjectFind(0, nm) < 0) ObjectCreate(0, nm, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   else { ObjectMove(0, nm, 0, t1, p1); ObjectMove(0, nm, 1, t2, p2); }
   ObjectSetInteger(0, nm, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nm, OBJPROP_FILL, true);
   ObjectSetInteger(0, nm, OBJPROP_BACK, true);
   SetCommon(nm);
}

void DrawArrow(string nm, datetime t, double p, int code, color c, ENUM_ARROW_ANCHOR a)
{
   if(ObjectFind(0, nm) < 0) ObjectCreate(0, nm, OBJ_ARROW, 0, t, p);
   else ObjectMove(0, nm, 0, t, p);
   ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, code);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, nm, OBJPROP_ANCHOR, a);
   SetCommon(nm);
}

double NormPrice(double p)
{
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(ts > 0) p = MathRound(p / ts) * ts;
   return NormalizeDouble(p, _Digits);
}

//+------------------------------------------------------------------+
//| SMC structs                                                      |
//+------------------------------------------------------------------+
struct BreakEvt
{
   datetime t;       // break bar
   int      dir;     // +1 bullish, -1 bearish
   bool     choch;
   double   level;   // broken pivot level
   datetime pivT;
   double   obTop;   // order block left behind by this move
   double   obBot;
   datetime obT;
};

struct OBRec
{
   datetime t;       // OB candle time
   datetime tBreak;  // structure break that created it
   datetime tMit;    // mitigation time (0 = still active)
   int      dir;     // +1 demand (bullish), -1 supply (bearish)
   double   top;
   double   bot;
   string   name;
};

//+------------------------------------------------------------------+
//| SMC engine: LuxAlgo legs, BOS/CHoCH, order blocks                |
//+------------------------------------------------------------------+
class CSMC
{
public:
   ENUM_TIMEFRAMES tf;
   int      len;
   string   tag;
   bool     drawStruct;
   bool     isSwing;
   bool     keepTrend;
   bool     keepOB;
   bool     drawOB;

   int      leg;
   double   hiLvl, loLvl;
   datetime hiT, loT;
   bool     hiX, loX;
   int      trend;
   datetime lastT;

   BreakEvt ev[];
   OBRec    ob[];
   datetime trT[];
   int      trV[];

   void Init(ENUM_TIMEFRAMES _tf, int _len, string _tag, bool _drawStruct, bool _swing,
             bool _keepTrend, bool _keepOB, bool _drawOB)
   {
      tf = _tf; len = MathMax(_len, 2); tag = _tag; drawStruct = _drawStruct; isSwing = _swing;
      keepTrend = _keepTrend; keepOB = _keepOB; drawOB = _drawOB;
      leg = -1; hiLvl = 0; loLvl = 0; hiT = 0; loT = 0; hiX = true; loX = true; trend = 0; lastT = 0;
      ArrayResize(ev, 0); ArrayResize(ob, 0); ArrayResize(trT, 0); ArrayResize(trV, 0);
   }

   bool Update(int initBars)
   {
      int count;
      if(lastT == 0) count = initBars + len + 3;
      else
      {
         int nb = Bars(_Symbol, tf, lastT, TimeCurrent());
         count = MathMax(nb, 0) + len + 5;
      }
      MqlRates r[];
      ArraySetAsSeries(r, false);
      int got = CopyRates(_Symbol, tf, 1, count, r);
      if(got < len + 3) return false;
      for(int k = len + 2; k < got; k++)
      {
         if(r[k].time <= lastT) continue;
         ProcessBar(r, k);
         lastT = r[k].time;
      }
      return true;
   }

   // LuxAlgo order block: bearish break -> candle with the highest high between pivot and break,
   //                      bullish break -> candle with the lowest low between pivot and break.
   bool FindOB(const MqlRates &r[], int k, int dir, datetime pivT, double &top, double &bot, datetime &t)
   {
      int best = -1;
      if(r[0].time <= pivT)
      for(int i = k - 1; i >= 0; i--)
      {
         if(r[i].time < pivT) break;
         if(best < 0) { best = i; continue; }
         if(dir == -1 && r[i].high > r[best].high) best = i;
         if(dir == 1  && r[i].low  < r[best].low)  best = i;
      }
      if(best < 0)
      {
         // pivot older than the loaded window: fetch the range directly
         MqlRates a[];
         ArraySetAsSeries(a, false);
         int n = CopyRates(_Symbol, tf, pivT, r[k].time, a);
         if(n < 2) return false;
         int b = 0;
         for(int i = 1; i < n - 1; i++)
         {
            if(dir == -1 && a[i].high > a[b].high) b = i;
            if(dir == 1  && a[i].low  < a[b].low)  b = i;
         }
         top = a[b].high; bot = a[b].low; t = a[b].time;
         return true;
      }
      top = r[best].high; bot = r[best].low; t = r[best].time;
      return true;
   }

   void ProcessBar(const MqlRates &r[], int k)
   {
      //--- legs / pivots
      int j = k - len;
      double mx = -DBL_MAX, mn = DBL_MAX;
      for(int i = j + 1; i <= k; i++)
      {
         if(r[i].high > mx) mx = r[i].high;
         if(r[i].low  < mn) mn = r[i].low;
      }
      int newLeg = leg;
      if(r[j].high > mx)      newLeg = 0;
      else if(r[j].low < mn)  newLeg = 1;
      if(newLeg != leg)
      {
         if(newLeg == 0) { hiLvl = r[j].high; hiT = r[j].time; hiX = false; }
         else            { loLvl = r[j].low;  loT = r[j].time; loX = false; }
         leg = newLeg;
      }

      //--- OB mitigation (HTF): close beyond the far edge
      if(keepOB)
      {
         for(int i = ArraySize(ob) - 1; i >= 0; i--)
         {
            if(ob[i].tMit != 0) continue;
            if((ob[i].dir == -1 && r[k].close > ob[i].top) || (ob[i].dir == 1 && r[k].close < ob[i].bot))
            {
               ob[i].tMit = r[k].time + PeriodSeconds(tf);
               if(drawOB) ObjectMove(0, ob[i].name, 1, r[k].time, ob[i].bot);
            }
         }
      }

      //--- structure breaks
      double c = r[k].close;
      if(!hiX && hiT > 0 && c > hiLvl)
      {
         bool ch = (trend == -1);
         hiX = true; trend = 1;
         OnBreak(r, k, 1, ch, hiLvl, hiT);
      }
      if(!loX && loT > 0 && c < loLvl)
      {
         bool ch = (trend == 1);
         loX = true; trend = -1;
         OnBreak(r, k, -1, ch, loLvl, loT);
      }

      if(keepTrend)
      {
         int n = ArraySize(trT);
         ArrayResize(trT, n + 1, 1000);
         ArrayResize(trV, n + 1, 1000);
         trT[n] = r[k].time;
         trV[n] = trend;
      }
   }

   void OnBreak(const MqlRates &r[], int k, int dir, bool ch, double lvl, datetime pT)
   {
      // a bullish break (dir=+1) leaves a demand OB = lowest candle since the pivot;
      // a bearish break (dir=-1) leaves a supply OB = highest candle since the pivot.
      double top = 0, bot = 0; datetime obT = 0;
      FindOB(r, k, dir, pT, top, bot, obT);

      int n = ArraySize(ev);
      if(n >= 2000) { for(int i = 1; i < n; i++) ev[i-1] = ev[i]; n = n - 1; }
      else ArrayResize(ev, n + 1, 200);
      ev[n].t = r[k].time; ev[n].dir = dir; ev[n].choch = ch; ev[n].level = lvl; ev[n].pivT = pT;
      ev[n].obTop = top; ev[n].obBot = bot; ev[n].obT = obT;

      if(drawStruct) DrawBreakObj(dir, ch, lvl, pT, r[k].time);
      if(keepOB && obT > 0) AddOB(dir, top, bot, obT, r[k].time);
   }

   void AddOB(int dir, double top, double bot, datetime t, datetime tBreak)
   {
      int n = ArraySize(ob);
      if(n >= 200)
      {
         if(drawOB) ObjectDelete(0, ob[0].name);
         for(int i = 1; i < n; i++) ob[i-1] = ob[i];
         n = n - 1;
      }
      else ArrayResize(ob, n + 1, 50);
      ob[n].t = t; ob[n].tBreak = tBreak; ob[n].tMit = 0; ob[n].dir = dir; ob[n].top = top; ob[n].bot = bot;
      ob[n].name = NewName(tag + "OB");
      if(drawOB)
      {
         DrawRect(ob[n].name, t, top, tBreak + PeriodSeconds(tf) * 5, bot, InpHTFOBColor);
         DrawText(ob[n].name + "_t", t, top, TFName(tf) + (dir == 1 ? " Demand OB" : " Supply OB"), clrDarkGoldenrod, 7, ANCHOR_LEFT_LOWER);
      }
      // keep only the most recent active OBs per direction
      int cnt = 0;
      for(int i = ArraySize(ob) - 1; i >= 0; i--)
      {
         if(ob[i].dir != dir || ob[i].tMit != 0) continue;
         cnt++;
         if(cnt > InpMaxHTFOB)
         {
            ob[i].tMit = tBreak;
            if(drawOB) ObjectMove(0, ob[i].name, 1, tBreak, ob[i].bot);
         }
      }
   }

   void RefreshOB()
   {
      if(!drawOB) return;
      datetime tEnd = TimeCurrent() + PeriodSeconds(tf) * 3;
      for(int i = 0; i < ArraySize(ob); i++)
         if(ob[i].tMit == 0) ObjectMove(0, ob[i].name, 1, tEnd, ob[i].bot);
   }

   void DrawBreakObj(int dir, bool choch, double lvl, datetime t1, datetime t2)
   {
      string nm = NewName(tag + "BR");
      Track(nm);
      color c = (dir == 1) ? InpBullColor : InpBearColor;
      DrawSeg(nm, t1, t2, lvl, c, isSwing ? STYLE_SOLID : STYLE_DASH, (tag == "H") ? 2 : 1);
      string txt = choch ? "CHoCH" : "BOS";
      if(tag == "H") txt = TFName(tf) + " " + txt;
      datetime tm = (datetime)(t1 + (t2 - t1) / 2);
      DrawText(nm + "_t", tm, lvl, txt, c, isSwing ? 9 : 7, dir == 1 ? ANCHOR_LOWER : ANCHOR_UPPER);
   }

   bool GetEventAt(datetime t, int dir, BreakEvt &e)
   {
      for(int i = ArraySize(ev) - 1; i >= 0; i--)
      {
         if(ev[i].t < t) break;
         if(ev[i].t == t && ev[i].dir == dir) { e = ev[i]; return true; }
      }
      return false;
   }

   int TrendAt(datetime t)
   {
      int sec = PeriodSeconds(tf);
      for(int i = ArraySize(trT) - 1; i >= 0; i--)
         if(trT[i] + sec <= t) return trV[i];
      return 0;
   }

   // most recent OB of direction dir that was active at time t and that the bar [lo,hi] touches
   int FindTouchedOB(int dir, datetime t, double hi, double lo)
   {
      int sec = PeriodSeconds(tf);
      for(int i = ArraySize(ob) - 1; i >= 0; i--)
      {
         if(ob[i].dir != dir) continue;
         if(ob[i].tBreak + sec > t) continue;               // not known yet
         if(ob[i].tMit != 0 && ob[i].tMit <= t) continue;   // already mitigated
         if(hi >= ob[i].bot && lo <= ob[i].top) return i;
      }
      return -1;
   }
};

CSMC htfEng;   // H1 trend + order blocks
CSMC ltfInt;   // M1 internal structure (CHoCH/MSS + M1 OB)
CSMC ltfSw;    // M1 swing structure (drawing)

//--- killzones
int    g_kzS[3], g_kzE[3];
bool   g_kzOn[3];
color  g_kzCol[3];
string g_kzName[3] = {"Asia", "London", "New York"};
long   g_kzTraded[3] = {-1, -1, -1};
string g_kzBox = "";
long   g_kzKey = -1;
double g_kzHi = 0, g_kzLo = 0;
datetime g_kzT0 = 0;

//--- strategy state
int      g_state = 0;        // 0 = waiting for price in POI, 1 = in POI, waiting for sweep + CHoCH
int      g_dir = 0;
double   g_zTop = 0, g_zBot = 0;
int      g_zOB = -1;         // index into htfEng.ob (or -1 for PDH/PDL)
string   g_zLbl = "";
double   g_ext = 0;
double   g_liq = 0;          // M1 swing level that must be swept
bool     g_swept = false;
datetime g_armT = 0;
int      g_bars = 0;
int      g_setupKZ = -1;
double   g_prevH = 0, g_prevL = 0;
string   g_used[];

bool     g_ready = false;
datetime g_lastLTF = 0;
datetime g_lastPanel = 0;

//+------------------------------------------------------------------+
//| Time / killzones                                                 |
//+------------------------------------------------------------------+
bool ParseKZ(string s, int &a, int &b)
{
   StringTrimLeft(s); StringTrimRight(s);
   if(StringLen(s) < 11) return false;
   a = (int)StringToInteger(StringSubstr(s, 0, 2)) * 60 + (int)StringToInteger(StringSubstr(s, 3, 2));
   b = (int)StringToInteger(StringSubstr(s, 6, 2)) * 60 + (int)StringToInteger(StringSubstr(s, 9, 2));
   return (a != b);
}

int ServerOffsetSec()
{
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION)) return InpTesterGMTOffsetMin * 60;
   long d = (long)(TimeTradeServer() - TimeGMT());
   return (int)(MathRound(d / 1800.0) * 1800);
}

long ISTSeconds(datetime serverT) { return (long)serverT - ServerOffsetSec() + 19800; }
int  ISTMinutes(datetime serverT) { long s = ISTSeconds(serverT); long m = ((s % 86400) + 86400) % 86400; return (int)(m / 60); }
long ISTDayKey(datetime serverT)  { return ISTSeconds(serverT) / 86400; }

int KZIndex(datetime t)
{
   int m = ISTMinutes(t);
   for(int i = 0; i < 3; i++)
   {
      if(!g_kzOn[i]) continue;
      if(g_kzS[i] < g_kzE[i]) { if(m >= g_kzS[i] && m < g_kzE[i]) return i; }
      else { if(m >= g_kzS[i] || m < g_kzE[i]) return i; }
   }
   return -1;
}

void UpdateKZBox(const MqlRates &b, int kz)
{
   if(!InpDrawKZ) return;
   if(kz < 0) { g_kzKey = -1; return; }
   long key = ISTDayKey(b.time) * 10 + kz;
   if(key != g_kzKey)
   {
      g_kzKey = key; g_kzHi = b.high; g_kzLo = b.low; g_kzT0 = b.time;
      g_kzBox = NewName("KZ");
      Track(g_kzBox);
   }
   else
   {
      g_kzHi = MathMax(g_kzHi, b.high);
      g_kzLo = MathMin(g_kzLo, b.low);
   }
   DrawRect(g_kzBox, g_kzT0, g_kzHi, b.time + PeriodSeconds(InpLTF), g_kzLo, g_kzCol[kz]);
   DrawText(g_kzBox + "_t", g_kzT0, g_kzHi, g_kzName[kz], clrGray, 8, ANCHOR_LEFT_LOWER);
}

bool Used(string key)
{
   for(int i = 0; i < ArraySize(g_used); i++) if(g_used[i] == key) return true;
   return false;
}

void MarkUsed(string key)
{
   int n = ArraySize(g_used);
   if(n >= 200) { for(int i = 1; i < n; i++) g_used[i-1] = g_used[i]; n = n - 1; }
   else ArrayResize(g_used, n + 1);
   g_used[n] = key;
}

//+------------------------------------------------------------------+
//| Account / risk                                                   |
//+------------------------------------------------------------------+
int OwnExposure()
{
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic && PositionGetString(POSITION_SYMBOL) == _Symbol) c++;
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) == InpMagic && OrderGetString(ORDER_SYMBOL) == _Symbol) c++;
   }
   return c;
}

void DailyStats(double &pnl, int &trades)
{
   pnl = 0; trades = 0;
   datetime now = TimeCurrent();
   MqlDateTime dt; TimeToStruct(now, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime d0 = StructToTime(dt);
   if(HistorySelect(d0, now + 60))
   {
      int n = HistoryDealsTotal();
      for(int i = 0; i < n; i++)
      {
         ulong tk = HistoryDealGetTicket(i);
         if(tk == 0) continue;
         if(HistoryDealGetInteger(tk, DEAL_MAGIC) != InpMagic) continue;
         if(HistoryDealGetString(tk, DEAL_SYMBOL) != _Symbol) continue;
         if(HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_IN) trades++;
         pnl += HistoryDealGetDouble(tk, DEAL_PROFIT) + HistoryDealGetDouble(tk, DEAL_COMMISSION) +
                HistoryDealGetDouble(tk, DEAL_SWAP);
      }
   }
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      pnl += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   }
}

bool TradingAllowed(string &why)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
   { why = "Algo trading disabled"; return false; }
   if(OwnExposure() > 0) { why = "trade/order already open"; return false; }
   double pnl; int trades;
   DailyStats(pnl, trades);
   if(InpMaxTradesPerDay > 0 && trades >= InpMaxTradesPerDay) { why = "max trades per day reached"; return false; }
   if(InpMaxDailyLossMoney > 0 && pnl <= -InpMaxDailyLossMoney) { why = "daily loss limit hit"; return false; }
   if(InpMaxSpreadPoints > 0)
   {
      long spr = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spr > InpMaxSpreadPoints) { why = "spread too high"; return false; }
   }
   return true;
}

double CalcLots(double riskPrice)
{
   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   double lots;
   if(InpLotMode == LOT_FIXED) lots = InpFixedLot;
   else
   {
      double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tv <= 0 || ts <= 0 || riskPrice <= 0) return 0;
      double money = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
      lots = money / (riskPrice / ts * tv);
   }
   if(InpMaxLot > 0) lots = MathMin(lots, InpMaxLot);
   lots = MathFloor(lots / step + 1e-9) * step;
   if(lots < minL)
   {
      if(InpLotMode == LOT_RISK_PERCENT && !InpAllowMinLot)
      {
         Print("SweepOB: risk-based lot below broker minimum -> setup skipped (enable InpAllowMinLot to force min lot)");
         return 0;
      }
      lots = minL;
   }
   lots = MathMin(lots, maxL);
   int digits = (int)MathMax(0, MathCeil(-MathLog10(step)));
   return NormalizeDouble(lots, digits);
}

//+------------------------------------------------------------------+
//| Setup drawings                                                   |
//+------------------------------------------------------------------+
void DrawArm(int dir, const MqlRates &b)
{
   if(!InpDrawSetups) return;
   string nm = NewName("ARM");
   Track(nm);
   DrawText(nm, b.time, dir == -1 ? b.high : b.low, "in " + g_zLbl, InpSweepColor, 7,
            dir == -1 ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
}

void DrawSweepMark(int dir, const MqlRates &b)
{
   if(!InpDrawSetups) return;
   string nm = NewName("SW");
   Track(nm);
   if(dir == -1) DrawArrow(nm, b.time, b.high, 234, InpSweepColor, ANCHOR_BOTTOM);
   else          DrawArrow(nm, b.time, b.low, 233, InpSweepColor, ANCHOR_TOP);
   DrawText(nm + "_t", b.time, dir == -1 ? b.high : b.low, "  SWEEP", InpSweepColor, 7,
            dir == -1 ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
}

void DrawSetup(const BreakEvt &e, datetime t, double entry, double sl, double tp, string txt)
{
   if(!InpDrawSetups) return;
   int sec = PeriodSeconds(InpLTF);
   string nm = NewName("MSS");
   Track(nm);
   DrawSeg(nm, e.pivT, t, e.level, InpMSSColor, STYLE_SOLID, 2);
   DrawText(nm + "_t", t, e.level, " MSS", InpMSSColor, 9, e.dir == 1 ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
   string o = NewName("LOB");
   Track(o);
   DrawRect(o, e.obT, e.obTop, t + sec * 10, e.obBot, InpLTFOBColor);
   DrawText(o + "_t", e.obT, e.dir == 1 ? e.obBot : e.obTop, "OB", clrDimGray, 8, e.dir == 1 ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER);
   if(entry <= 0) return;
   string rk = NewName("RISK");
   Track(rk);
   DrawRect(rk, t, sl, t + sec * 40, entry, C'255,205,210');
   string rw = NewName("RWD");
   Track(rw);
   DrawRect(rw, t, entry, t + sec * 40, tp, C'200,235,215');
   DrawText(rw + "_t", t, tp, txt, e.dir == 1 ? InpBullColor : InpBearColor, 8, e.dir == 1 ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
}

void DrawLiveLevels()
{
   if(!InpDrawLevels) return;
   datetime tEnd = TimeCurrent() + PeriodSeconds(InpHTF);
   double dH = iHigh(_Symbol, PERIOD_D1, 1), dL = iLow(_Symbol, PERIOD_D1, 1);
   datetime dT = iTime(_Symbol, PERIOD_D1, 1);
   if(dH > 0)
   {
      DrawSeg(PFX + "DH", dT, tEnd, dH, clrBlack, STYLE_SOLID, 1);
      DrawText(PFX + "DH_t", tEnd, dH, "PDH", clrBlack, 8, ANCHOR_RIGHT_LOWER);
      DrawSeg(PFX + "DL", dT, tEnd, dL, clrBlack, STYLE_SOLID, 1);
      DrawText(PFX + "DL_t", tEnd, dL, "PDL", clrBlack, 8, ANCHOR_RIGHT_UPPER);
   }
}

//+------------------------------------------------------------------+
//| Entry plan (shared by live trading and history markers)          |
//+------------------------------------------------------------------+
bool PlanTrade(int dir, const BreakEvt &e, double ext, double &entry, double &sl, double &tp)
{
   double pt = _Point;
   double edge = (dir == -1) ? e.obBot : e.obTop;
   double mid  = (e.obTop + e.obBot) / 2.0;
   entry = (InpOBLevel == OB_NEAR_EDGE) ? edge : mid;
   double far = (dir == -1) ? MathMax(e.obTop, ext) : MathMin(e.obBot, ext);
   sl = (dir == -1) ? far + InpSLBufferPoints * pt : far - InpSLBufferPoints * pt;
   if((dir == -1 && sl <= entry) || (dir == 1 && sl >= entry)) return false;
   double risk = MathAbs(entry - sl);
   if(InpMaxSLPoints > 0 && risk / pt > InpMaxSLPoints) return false;
   tp = (dir == -1) ? entry - InpRR * risk : entry + InpRR * risk;
   return true;
}

bool TryEnter(int dir, const BreakEvt &e, double ext, const MqlRates &b)
{
   string why = "";
   if(!TradingAllowed(why)) { Print("SweepOB: setup skipped - ", why); return false; }
   double entry, sl, tp;
   if(!PlanTrade(dir, e, ext, entry, sl, tp)) { Print("SweepOB: invalid / too wide SL, skipped"); return false; }

   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) return false;
   bool market = (dir == -1) ? (tk.bid >= entry) : (tk.ask <= entry);   // already inside the OB
   if(market)
   {
      entry = (dir == -1) ? tk.bid : tk.ask;
      double risk = MathAbs(entry - sl);
      if((dir == -1 && sl <= entry) || (dir == 1 && sl >= entry)) return false;
      tp = (dir == -1) ? entry - InpRR * risk : entry + InpRR * risk;
   }
   entry = NormPrice(entry); sl = NormPrice(sl); tp = NormPrice(tp);
   double risk = MathAbs(entry - sl);
   long stopsLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(risk < stopsLvl * _Point) { Print("SweepOB: SL inside stops level, skipped"); return false; }
   double lots = CalcLots(risk);
   if(lots <= 0) return false;

   bool ok;
   if(market)
      ok = (dir == -1) ? trade.Sell(lots, _Symbol, 0.0, sl, tp, InpComment)
                       : trade.Buy(lots, _Symbol, 0.0, sl, tp, InpComment);
   else
      ok = (dir == -1) ? trade.SellLimit(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, InpComment)
                       : trade.BuyLimit(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, InpComment);
   if(!ok)
   {
      Print("SweepOB: order failed - ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
      return false;
   }
   PrintFormat("SweepOB: %s %s %.2f lots @ %s SL %s TP %s (%s)", dir == -1 ? "SELL" : "BUY",
               market ? "market" : "limit", lots, DoubleToString(entry, _Digits),
               DoubleToString(sl, _Digits), DoubleToString(tp, _Digits), g_zLbl);
   DrawSetup(e, b.time, entry, sl, tp, (dir == -1 ? "SELL " : "BUY ") + (market ? "mkt" : "limit"));
   return true;
}

//+------------------------------------------------------------------+
//| Strategy (once per closed LTF bar)                               |
//+------------------------------------------------------------------+
void Arm(int dir, double top, double bot, int obIdx, string lbl, string key, int kz, const MqlRates &b)
{
   g_state = 1; g_dir = dir; g_zTop = top; g_zBot = bot; g_zOB = obIdx; g_zLbl = lbl;
   g_ext = (dir == -1) ? b.high : b.low;
   g_armT = b.time; g_bars = 0; g_setupKZ = kz;
   g_liq = (dir == -1) ? ltfInt.hiLvl : ltfInt.loLvl;
   g_swept = (g_liq <= 0) || (dir == -1 ? b.high > g_liq : b.low < g_liq);
   MarkUsed(key);
   DrawArm(dir, b);
   if(g_swept && g_liq > 0) DrawSweepMark(dir, b);
}

void StrategyCore(const MqlRates &b, bool live)
{
   datetime closeT = b.time + PeriodSeconds(InpLTF);
   int kz = KZIndex(b.time);
   UpdateKZBox(b, kz);
   int bias = htfEng.TrendAt(closeT);

   if(g_state == 1)
   {
      g_bars++;
      if(g_dir == -1) g_ext = MathMax(g_ext, b.high); else g_ext = MathMin(g_ext, b.low);
      if(!g_swept && g_liq > 0 && ((g_dir == -1 && b.high > g_liq) || (g_dir == 1 && b.low < g_liq)))
      {
         g_swept = true;
         DrawSweepMark(g_dir, b);
      }
      bool cancel = (g_bars > InpMaxBarsInZone);
      if(InpBiasMode == BIAS_HTF_STRUCTURE && bias != g_dir) cancel = true;
      if(g_zOB >= 0 && g_zOB < ArraySize(htfEng.ob) && htfEng.ob[g_zOB].tMit != 0 && htfEng.ob[g_zOB].tMit <= closeT) cancel = true;

      BreakEvt e;
      if(!cancel && ltfInt.GetEventAt(b.time, g_dir, e) && (!InpRequireCHoCH || e.choch) &&
         (!InpRequireSweep || g_swept) && e.obT > 0)
      {
         bool done = false;
         if(live) done = TryEnter(g_dir, e, g_ext, b);
         else
         {
            double en, sl, tp;
            if(PlanTrade(g_dir, e, g_ext, en, sl, tp))
            {
               DrawSetup(e, b.time, en, sl, tp, g_dir == -1 ? "SELL setup" : "BUY setup");
               done = true;
            }
         }
         if(done && g_setupKZ >= 0) g_kzTraded[g_setupKZ] = ISTDayKey(b.time) * 10 + g_setupKZ;
         g_state = 0;
         return;
      }
      if(cancel) g_state = 0;
      else return;
   }

   if(kz < 0) return;
   if(InpOneTradePerKillzone && g_kzTraded[kz] == ISTDayKey(b.time) * 10 + kz) return;
   bool canSell = (InpBiasMode == BIAS_BOTH_SIDES) || bias == -1;
   bool canBuy  = (InpBiasMode == BIAS_BOTH_SIDES) || bias == 1;

   for(int pass = 0; pass < 2; pass++)
   {
      int dir = (pass == 0) ? -1 : 1;
      if((dir == -1 && !canSell) || (dir == 1 && !canBuy)) continue;
      if(InpUseHTFOB)
      {
         int i = htfEng.FindTouchedOB(dir, closeT, b.high, b.low);
         if(i >= 0)
         {
            string key = "O|" + IntegerToString((long)htfEng.ob[i].t) + "|" + IntegerToString(dir);
            if(!Used(key))
            {
               Arm(dir, htfEng.ob[i].top, htfEng.ob[i].bot, i,
                   TFName(InpHTF) + (dir == -1 ? " supply OB" : " demand OB"), key, kz, b);
               return;
            }
         }
      }
      if(InpUsePrevDay)
      {
         int dsh = iBarShift(_Symbol, PERIOD_D1, b.time, false);
         if(dsh < 0) continue;
         double lvl = (dir == -1) ? iHigh(_Symbol, PERIOD_D1, dsh + 1) : iLow(_Symbol, PERIOD_D1, dsh + 1);
         datetime dT = iTime(_Symbol, PERIOD_D1, dsh + 1);
         string key = "D|" + IntegerToString((long)dT) + "|" + IntegerToString(dir);
         bool crossed = (dir == -1) ? (b.high > lvl && g_prevH <= lvl) : (b.low < lvl && g_prevL >= lvl);
         if(lvl > 0 && crossed && !Used(key))
         {
            Arm(dir, lvl, lvl, -1, dir == -1 ? "PDH" : "PDL", key, kz, b);
            return;
         }
      }
   }
}

void StrategyStep(const MqlRates &b, bool live)
{
   StrategyCore(b, live);
   g_prevH = b.high;
   g_prevL = b.low;
}

//+------------------------------------------------------------------+
//| Trade management                                                 |
//+------------------------------------------------------------------+
void ManageOpen()
{
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) return;
   int ltfSec = PeriodSeconds(InpLTF);
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic || OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      datetime setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      long type = OrderGetInteger(ORDER_TYPE);
      double osl = OrderGetDouble(ORDER_SL);
      bool kill = (TimeCurrent() - setup > (long)InpLimitExpiryBars * ltfSec);
      if(type == ORDER_TYPE_SELL_LIMIT && osl > 0 && tk.bid >= osl) kill = true;
      if(type == ORDER_TYPE_BUY_LIMIT  && osl > 0 && tk.ask <= osl) kill = true;
      if(kill) trade.OrderDelete(t);
   }
   if(InpBreakEvenR <= 0) return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic || PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      double op = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      long type = PositionGetInteger(POSITION_TYPE);
      if(sl <= 0) continue;
      if(type == POSITION_TYPE_SELL && sl > op && (op - tk.ask) >= InpBreakEvenR * (sl - op))
         trade.PositionModify(t, NormPrice(op), tp);
      if(type == POSITION_TYPE_BUY && sl < op && (tk.bid - op) >= InpBreakEvenR * (op - sl))
         trade.PositionModify(t, NormPrice(op), tp);
   }
}

void UpdatePanel()
{
   int bias = htfEng.trend;
   int kz = KZIndex(TimeCurrent());
   double pnl; int trades;
   DailyStats(pnl, trades);
   int act = 0;
   for(int i = 0; i < ArraySize(htfEng.ob); i++) if(htfEng.ob[i].tMit == 0) act++;
   string s = "SweepOB EA  |  " + _Symbol + "  " + TFName(InpLTF) + "\n";
   s += "H1 trend: " + (bias == 1 ? "BULLISH -> wait for price in a DEMAND OB" :
                        bias == -1 ? "BEARISH -> wait for price in a SUPPLY OB" : "none yet") + "\n";
   s += "Active " + TFName(InpHTF) + " order blocks: " + IntegerToString(act) + "\n";
   s += "Killzone: " + (kz >= 0 ? g_kzName[kz] : "outside") + "\n";
   if(g_state == 1)
      s += StringFormat("State: price in %s, %s, waiting for CHoCH  %d/%d bars\n", g_zLbl,
                        g_swept ? "liquidity swept" : "waiting for sweep", g_bars, InpMaxBarsInZone);
   else s += "State: waiting for price to reach a POI\n";
   s += StringFormat("Today: %d trades, P&L %.2f %s\n", trades, pnl, AccountInfoString(ACCOUNT_CURRENCY));
   Comment(s);
}

//+------------------------------------------------------------------+
//| Init / history                                                   |
//+------------------------------------------------------------------+
bool BuildHistory()
{
   if(!htfEng.Update(InpHTFHistoryBars)) return false;
   if(!ltfSw.Update(InpHistoryBars))     return false;
   if(!ltfInt.Update(InpHistoryBars))    return false;

   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, InpLTF, 1, InpHistoryBars, r);
   if(got <= 0) return false;
   for(int i = 0; i < got; i++) StrategyStep(r[i], false);
   for(int i = 0; i < 3; i++) g_kzTraded[i] = -1;

   htfEng.RefreshOB();
   DrawLiveLevels();
   g_lastLTF = iTime(_Symbol, InpLTF, 0);
   g_ready = true;
   UpdatePanel();
   ChartRedraw();
   Print("SweepOB: history marked (", got, " bars). H1 trend ", htfEng.trend);
   return true;
}

int OnInit()
{
   string kz[3];
   kz[0] = InpKZ1; kz[1] = InpKZ2; kz[2] = InpKZ3;
   for(int i = 0; i < 3; i++) g_kzOn[i] = ParseKZ(kz[i], g_kzS[i], g_kzE[i]);
   g_kzCol[0] = InpKZ1Color; g_kzCol[1] = InpKZ2Color; g_kzCol[2] = InpKZ3Color;

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(30);

   //          tf      len             tag  drawStruct        swing  trend  keepOB drawOB
   htfEng.Init(InpHTF, InpHTFLen,      "H", InpDrawHTFStruct, true,  true,  true,  InpDrawHTFOB);
   ltfInt.Init(InpLTF, InpLTFLen,      "I", InpDrawLTFStruct, false, false, false, false);
   ltfSw.Init (InpLTF, InpLTFSwingLen, "S", InpDrawLTFSwing,  true,  false, false, false);

   if(_Period != InpLTF)
      Print("SweepOB: tip - attach the EA to a ", TFName(InpLTF), " chart so the markings line up.");

   g_ready = false;
   g_state = 0;
   ArrayResize(g_used, 0);
   ArrayResize(g_ring, 0);
   EventSetTimer(2);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, PFX);
   Comment("");
}

void OnTimer()
{
   if(!g_ready) BuildHistory();
}

void OnNewLTFBar()
{
   htfEng.Update(0);
   ltfSw.Update(0);
   ltfInt.Update(0);
   htfEng.RefreshOB();
   MqlRates r[];
   ArraySetAsSeries(r, false);
   if(CopyRates(_Symbol, InpLTF, 1, 1, r) != 1) return;
   StrategyStep(r[0], true);
   DrawLiveLevels();
}

void OnTick()
{
   if(!g_ready) { if(!BuildHistory()) return; }
   datetime t0 = iTime(_Symbol, InpLTF, 0);
   if(t0 > 0 && t0 != g_lastLTF)
   {
      g_lastLTF = t0;
      OnNewLTFBar();
   }
   ManageOpen();
   if(TimeCurrent() - g_lastPanel >= 2)
   {
      g_lastPanel = TimeCurrent();
      UpdatePanel();
   }
}
//+------------------------------------------------------------------+
