export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  public: {
    Tables: {
      analysis: {
        Row: {
          adx: Json | null
          atr: number | null
          bollinger_bands: Json | null
          cci: number | null
          change24h: number | null
          created_at: string | null
          ema: number | null
          id: number
          last_price: number
          macd: Json | null
          momentum: number | null
          obv: number | null
          open_interest: number | null
          rating: number | null
          rsi: number | null
          signals: Json | null
          sma: number | null
          stochastic_rsi: Json | null
          symbol: string
          trend: string
          volume24h: number
        }
        Insert: {
          adx?: Json | null
          atr?: number | null
          bollinger_bands?: Json | null
          cci?: number | null
          change24h?: number | null
          created_at?: string | null
          ema?: number | null
          id?: never
          last_price: number
          macd?: Json | null
          momentum?: number | null
          obv?: number | null
          open_interest?: number | null
          rating?: number | null
          rsi?: number | null
          signals?: Json | null
          sma?: number | null
          stochastic_rsi?: Json | null
          symbol: string
          trend: string
          volume24h?: number
        }
        Update: {
          adx?: Json | null
          atr?: number | null
          bollinger_bands?: Json | null
          cci?: number | null
          change24h?: number | null
          created_at?: string | null
          ema?: number | null
          id?: never
          last_price?: number
          macd?: Json | null
          momentum?: number | null
          obv?: number | null
          open_interest?: number | null
          rating?: number | null
          rsi?: number | null
          signals?: Json | null
          sma?: number | null
          stochastic_rsi?: Json | null
          symbol?: string
          trend?: string
          volume24h?: number
        }
        Relationships: []
      }
      news: {
        Row: {
          content: string
          id: number
          market_reaction: string | null
          price_change: number | null
          published_at: string | null
          sentiment_score: number | null
          title: string
          trading_volume: number | null
          volatility_index: number | null
        }
        Insert: {
          content: string
          id?: never
          market_reaction?: string | null
          price_change?: number | null
          published_at?: string | null
          sentiment_score?: number | null
          title: string
          trading_volume?: number | null
          volatility_index?: number | null
        }
        Update: {
          content?: string
          id?: never
          market_reaction?: string | null
          price_change?: number | null
          published_at?: string | null
          sentiment_score?: number | null
          title?: string
          trading_volume?: number | null
          volatility_index?: number | null
        }
        Relationships: []
      }
      tickers_spot: {
        Row: {
          high_price_24h: number
          id: number
          last_price: number
          low_price_24h: number
          prev_price_24h: number
          price_24h_pcnt: number
          symbol: string
          turnover_24h: number
          usd_index_price: number
          volume_24h: number
        }
        Insert: {
          high_price_24h: number
          id?: never
          last_price: number
          low_price_24h: number
          prev_price_24h: number
          price_24h_pcnt: number
          symbol: string
          turnover_24h: number
          usd_index_price: number
          volume_24h: number
        }
        Update: {
          high_price_24h?: number
          id?: never
          last_price?: number
          low_price_24h?: number
          prev_price_24h?: number
          price_24h_pcnt?: number
          symbol?: string
          turnover_24h?: number
          usd_index_price?: number
          volume_24h?: number
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      get_most_recent_unique_symbols: {
        Args: Record<PropertyKey, never>
        Returns: {
          adx: Json | null
          atr: number | null
          bollinger_bands: Json | null
          cci: number | null
          change24h: number | null
          created_at: string | null
          ema: number | null
          id: number
          last_price: number
          macd: Json | null
          momentum: number | null
          obv: number | null
          open_interest: number | null
          rating: number | null
          rsi: number | null
          signals: Json | null
          sma: number | null
          stochastic_rsi: Json | null
          symbol: string
          trend: string
          volume24h: number
        }[]
      }
      get_unique_symbols: {
        Args: Record<PropertyKey, never>
        Returns: string
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type PublicSchema = Database[Extract<keyof Database, "public">]

export type Tables<
  PublicTableNameOrOptions extends
    | keyof (PublicSchema["Tables"] & PublicSchema["Views"])
    | { schema: keyof Database },
  TableName extends PublicTableNameOrOptions extends { schema: keyof Database }
    ? keyof (Database[PublicTableNameOrOptions["schema"]]["Tables"] &
        Database[PublicTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = PublicTableNameOrOptions extends { schema: keyof Database }
  ? (Database[PublicTableNameOrOptions["schema"]]["Tables"] &
      Database[PublicTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : PublicTableNameOrOptions extends keyof (PublicSchema["Tables"] &
        PublicSchema["Views"])
    ? (PublicSchema["Tables"] &
        PublicSchema["Views"])[PublicTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  PublicTableNameOrOptions extends
    | keyof PublicSchema["Tables"]
    | { schema: keyof Database },
  TableName extends PublicTableNameOrOptions extends { schema: keyof Database }
    ? keyof Database[PublicTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = PublicTableNameOrOptions extends { schema: keyof Database }
  ? Database[PublicTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : PublicTableNameOrOptions extends keyof PublicSchema["Tables"]
    ? PublicSchema["Tables"][PublicTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  PublicTableNameOrOptions extends
    | keyof PublicSchema["Tables"]
    | { schema: keyof Database },
  TableName extends PublicTableNameOrOptions extends { schema: keyof Database }
    ? keyof Database[PublicTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = PublicTableNameOrOptions extends { schema: keyof Database }
  ? Database[PublicTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : PublicTableNameOrOptions extends keyof PublicSchema["Tables"]
    ? PublicSchema["Tables"][PublicTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  PublicEnumNameOrOptions extends
    | keyof PublicSchema["Enums"]
    | { schema: keyof Database },
  EnumName extends PublicEnumNameOrOptions extends { schema: keyof Database }
    ? keyof Database[PublicEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = PublicEnumNameOrOptions extends { schema: keyof Database }
  ? Database[PublicEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : PublicEnumNameOrOptions extends keyof PublicSchema["Enums"]
    ? PublicSchema["Enums"][PublicEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof PublicSchema["CompositeTypes"]
    | { schema: keyof Database },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof Database
  }
    ? keyof Database[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends { schema: keyof Database }
  ? Database[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof PublicSchema["CompositeTypes"]
    ? PublicSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never
