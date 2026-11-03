export interface Analyst {
  key: string;
  displayName: string;
  description: string;
  investingStyle: string;
  order: number;
}

const values: Array<Omit<Analyst, "order">> = [
  ["aswath_damodaran", "Aswath Damodaran", "The Dean of Valuation", "Intrinsic value and rigorous valuation analysis."],
  ["ben_graham", "Ben Graham", "The Father of Value Investing", "Margin of safety and systematic value analysis."],
  ["bill_ackman", "Bill Ackman", "The Activist Investor", "Strategic activism and concentrated contrarian positions."],
  ["cathie_wood", "Cathie Wood", "The Queen of Growth Investing", "Disruptive innovation and long-duration growth."],
  ["charlie_munger", "Charlie Munger", "The Rational Thinker", "Quality businesses, mental models and patient ownership."],
  ["michael_burry", "Michael Burry", "The Big Short Contrarian", "Deep fundamental analysis and contrarian bets."],
  ["mohnish_pabrai", "Mohnish Pabrai", "The Dhandho Investor", "Low-risk, high-uncertainty value opportunities."],
  ["nassim_taleb", "Nassim Taleb", "The Black Swan Risk Analyst", "Tail risk, antifragility and asymmetric payoffs."],
  ["peter_lynch", "Peter Lynch", "The 10-Bagger Investor", "Understandable businesses with durable growth."],
  ["phil_fisher", "Phil Fisher", "The Scuttlebutt Investor", "Management quality, innovation and field research."],
  ["rakesh_jhunjhunwala", "Rakesh Jhunjhunwala", "The Big Bull Of India", "Macroeconomic insight and emerging-market growth."],
  ["stanley_druckenmiller", "Stanley Druckenmiller", "The Macro Investor", "Top-down macro trends and asymmetric conviction."],
  ["warren_buffett", "Warren Buffett", "The Oracle of Omaha", "Competitive advantages and long-term value investing."],
  ["technical_analyst", "Technical Analyst", "Chart Pattern Specialist", "Price action, trends and technical indicators."],
  ["fundamentals_analyst", "Fundamentals Analyst", "Financial Statement Specialist", "Financial statements and economic fundamentals."],
  ["growth_analyst", "Growth Analyst", "Growth Specialist", "Growth quality, persistence and valuation."],
  ["news_sentiment_analyst", "News Sentiment Analyst", "News Sentiment Specialist", "News flow and its effect on market expectations."],
  ["sentiment_analyst", "Sentiment Analyst", "Market Sentiment Specialist", "Investor behavior and market positioning."],
  ["valuation_analyst", "Valuation Analyst", "Company Valuation Specialist", "Fair value using financial models and market evidence."],
].map(([key, displayName, description, investingStyle]) => ({
  key,
  displayName,
  description,
  investingStyle,
}));

export const analysts: readonly Analyst[] = values.map((value, order) => ({
  ...value,
  order,
}));

