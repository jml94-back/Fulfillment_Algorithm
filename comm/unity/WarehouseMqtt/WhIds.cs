// 장비·위치 ID 와 랙 높이 규칙. Python whmqtt/ids.py, Warehouse_System_Spec 2장과 같다.
//   통로 R1~R3 · 리프트 L1~L3 · 모셔틀 R1-Shuttle_L01~L10 · 버퍼 R1-IN / R1-OUT (칸 IN1~IN5, 1 = 리프트 쪽)
//   채널 R2-E07-L03 (줄 01 = 리프트 쪽) · 칸 R2-E07-L03-14 (01 = 통로 쪽 앞칸) · 랙 팔레트 R1-000123
namespace Warehouse.Mqtt
{
    /// 장비·위치 ID 규칙 (Python whmqtt/ids.py 와 같음)
    public static class WhIds
    {
        public const float RailTop0 = 0.40f, LevelHeight = 1.45f, BufferTop = 0.50f;
        public static string Shuttle(string rail, int level) => $"{rail}-Shuttle_L{level:00}";
        /// side 'W' / 'E', row 1~33 (리프트 쪽부터), level 1~10
        public static string Channel(string rail, char side, int row, int level) => $"{rail}-{side}{row:00}-L{level:00}";
        /// slot 1~14 (01 = 통로 쪽 앞칸)
        public static string Slot(string channel, int slot) => $"{channel}-{slot:00}";
        public static string Lift(string rail) => "L" + rail.Substring(1);
    }

}
