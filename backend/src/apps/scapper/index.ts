import { Scraper, SearchMode } from "agent-twitter-client"

export async function searchTweets(query: string, mode?: "latest" | "top") {
  const scraper = new Scraper()

  await scraper.login(
    process.env.TWITTER_USERNAME!,
    process.env.TWITTER_PASSWORD!,
    process.env.TWITTER_EMAIL
  )

  const iterator = scraper.searchTweets(query, 10, SearchMode.Latest)

  for await (const value of iterator) {
    console.log(value)
  }
}
