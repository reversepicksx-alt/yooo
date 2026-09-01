import React, { useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Modal,
  ScrollView,
  Platform
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Colors from '@/constants/colors';
import type { Pick } from '@/lib/api';

// Helper to get local YYYY-MM-DD from a Date object
const toISODate = (d: Date) => {
  const pad = (n: number) => (n < 10 ? '0' + n : n);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const getDaysInMonth = (year: number, month: number) => new Date(year, month + 1, 0).getDate();
const getFirstDayOfMonth = (year: number, month: number) => new Date(year, month, 1).getDay();

export interface PicksCalendarProps {
  picks: Pick[];
  onClose: () => void;
  visible?: boolean;
}

export default function PicksCalendar({ picks, onClose, visible = true }: PicksCalendarProps) {
  const insets = useSafeAreaInsets();
  
  const [currentDate, setCurrentDate] = useState(() => new Date());
  const [selectedDateStr, setSelectedDateStr] = useState<string | null>(null);
  
  const currentYear = currentDate.getFullYear();
  const currentMonth = currentDate.getMonth();

  // Group picks by date
  const picksByDate = useMemo(() => {
    const map: Record<string, Pick[]> = {};
    for (const p of picks) {
      const dateStrRaw = p.createdAt || p.settledAt;
      if (!dateStrRaw) continue;
      const d = new Date(dateStrRaw);
      if (isNaN(d.getTime())) continue;
      const iso = toISODate(d);
      if (!map[iso]) map[iso] = [];
      map[iso].push(p);
    }
    return map;
  }, [picks]);

  const nextMonth = () => {
    setCurrentDate(new Date(currentYear, currentMonth + 1, 1));
  };

  const prevMonth = () => {
    setCurrentDate(new Date(currentYear, currentMonth - 1, 1));
  };
  
  // Calculate days for the grid
  const daysInMonth = getDaysInMonth(currentYear, currentMonth);
  const firstDay = getFirstDayOfMonth(currentYear, currentMonth);
  
  const daysArray = [];
  for (let i = 0; i < firstDay; i++) {
    daysArray.push(null);
  }
  for (let i = 1; i <= daysInMonth; i++) {
    daysArray.push(i);
  }
  while (daysArray.length % 7 !== 0) {
    daysArray.push(null);
  }
  
  const monthNames = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  const weekDays = ["S", "M", "T", "W", "T", "F", "S"];

  const handleDayPress = (day: number) => {
    const d = new Date(currentYear, currentMonth, day);
    setSelectedDateStr(toISODate(d));
  };

  // Helper formatting for bottom sheet
  const selectedDateObj = selectedDateStr ? new Date(selectedDateStr + 'T00:00:00') : null;
  const selectedDateLabel = selectedDateObj 
    ? selectedDateObj.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    : '';

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Picks Calendar</Text>
          <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
            <Ionicons name="close" size={24} color={Colors.text} />
          </TouchableOpacity>
        </View>
        
        <View style={styles.monthNav}>
          <TouchableOpacity onPress={prevMonth} style={styles.navBtn}>
            <Ionicons name="chevron-back" size={20} color={Colors.text} />
          </TouchableOpacity>
          <Text style={styles.monthText}>{monthNames[currentMonth]} {currentYear}</Text>
          <TouchableOpacity onPress={nextMonth} style={styles.navBtn}>
            <Ionicons name="chevron-forward" size={20} color={Colors.text} />
          </TouchableOpacity>
        </View>
        
        <View style={styles.weekDaysRow}>
          {weekDays.map((wd, i) => (
            <Text key={`wd-${i}`} style={styles.weekDayText}>{wd}</Text>
          ))}
        </View>
        
        <View style={styles.grid}>
          {daysArray.map((day, i) => {
            if (day === null) {
              return <View key={`empty-${i}`} style={styles.cell} />;
            }
            
            const dateStr = toISODate(new Date(currentYear, currentMonth, day));
            const dayPicks = picksByDate[dateStr] || [];
            const isSelected = selectedDateStr === dateStr;
            const isToday = dateStr === toISODate(new Date());
            
            return (
              <View key={`day-${day}`} style={styles.cell}>
                <TouchableOpacity 
                  style={[
                    styles.cellInner, 
                    isSelected && styles.cellSelected,
                    isToday && !isSelected && styles.cellToday
                  ]} 
                  onPress={() => handleDayPress(day)}
                  activeOpacity={0.7}
                >
                  <Text style={[
                    styles.dayText, 
                    isSelected && styles.dayTextSelected,
                    isToday && !isSelected && styles.dayTextToday
                  ]}>
                    {day}
                  </Text>
                  
                  {dayPicks.length > 0 && (
                    <View style={styles.dotsRow}>
                      {dayPicks.slice(0, 4).map((p, j) => {
                        const color = p.result === 'hit' ? Colors.success :
                                      p.result === 'miss' ? Colors.error :
                                      p.result === 'push' ? Colors.push :
                                      Colors.textTertiary;
                        return <View key={j} style={[styles.dot, { backgroundColor: color }]} />
                      })}
                      {dayPicks.length > 4 && (
                        <View style={styles.plusDot}>
                          <Text style={styles.plusDotText}>+</Text>
                        </View>
                      )}
                    </View>
                  )}
                </TouchableOpacity>
              </View>
            );
          })}
        </View>

        {selectedDateStr && (
          <View style={[styles.bottomSheet, { paddingBottom: Math.max(insets.bottom, 20) }]}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>
                {selectedDateLabel}
              </Text>
              <TouchableOpacity onPress={() => setSelectedDateStr(null)} style={styles.sheetCloseBtn}>
                <Ionicons name="close-circle" size={24} color={Colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <ScrollView style={styles.sheetScroll} showsVerticalScrollIndicator={false}>
              {(picksByDate[selectedDateStr] || []).length === 0 ? (
                <Text style={styles.emptyText}>No picks on this date.</Text>
              ) : (
                 (picksByDate[selectedDateStr] || []).map((p, i) => (
                    <MiniPickCard key={p._id || p.id || i} pick={p} />
                 ))
              )}
            </ScrollView>
          </View>
        )}
      </View>
    </Modal>
  );
}

function MiniPickCard({ pick }: { pick: Pick }) {
  const won = pick.result === 'hit';
  const lost = pick.result === 'miss';
  const push = pick.result === 'push';
  
  const statusColor = won ? Colors.success : lost ? Colors.error : push ? Colors.push : Colors.textTertiary;
  
  const propLabel = pick.propType?.replace(/_/g, ' ') || '—';
  
  return (
    <View style={styles.miniCard}>
      <View style={styles.miniCardHeader}>
        <Text style={styles.playerName}>{pick.playerName}</Text>
        <Text style={[styles.resultText, { color: statusColor }]}>
          {won ? 'HIT' : lost ? 'MISS' : push ? 'PUSH' : 'PENDING'}
        </Text>
      </View>
      <View style={styles.miniCardBody}>
        <Text style={styles.propText}>{propLabel}</Text>
        <Text style={styles.lineText}>
          {pick.recommendation === 'over' ? 'O' : pick.recommendation === 'under' ? 'U' : ''} {pick.line}
        </Text>
      </View>
      {pick.matchScore && (
        <Text style={styles.scoreText}>{pick.matchScore}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  headerTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: Colors.text,
  },
  closeBtn: {
    padding: 4,
  },
  monthNav: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingVertical: 16,
  },
  navBtn: {
    padding: 8,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 8,
  },
  monthText: {
    fontSize: 18,
    fontWeight: '600',
    color: Colors.text,
  },
  weekDaysRow: {
    flexDirection: 'row',
    paddingHorizontal: 10,
    marginBottom: 8,
  },
  weekDayText: {
    flex: 1,
    textAlign: 'center',
    fontSize: 13,
    fontWeight: '600',
    color: Colors.textSecondary,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    paddingHorizontal: 10,
  },
  cell: {
    width: '14.28%', // 100 / 7
    aspectRatio: 1,
    padding: 2,
  },
  cellInner: {
    flex: 1,
    borderRadius: 8,
    backgroundColor: Colors.card,
    padding: 4,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'transparent',
  },
  cellSelected: {
    borderColor: Colors.primary,
    backgroundColor: Colors.primaryDim,
  },
  cellToday: {
    borderColor: Colors.borderSubtle,
  },
  dayText: {
    fontSize: 14,
    fontWeight: '500',
    color: Colors.textSecondary,
    marginBottom: 4,
  },
  dayTextSelected: {
    color: Colors.primary,
    fontWeight: '700',
  },
  dayTextToday: {
    color: Colors.text,
    fontWeight: '700',
  },
  dotsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 2,
    marginTop: 'auto',
    marginBottom: 4,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  plusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: Colors.textTertiary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  plusDotText: {
    fontSize: 5,
    color: Colors.background,
    fontWeight: 'bold',
    lineHeight: 6,
    marginTop: Platform.OS === 'ios' ? 0.5 : 0,
  },
  bottomSheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: '55%',
    backgroundColor: Colors.cardSecondary,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -4 },
    shadowOpacity: 0.5,
    shadowRadius: 12,
    elevation: 20,
  },
  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  sheetTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: Colors.text,
  },
  sheetCloseBtn: {
    padding: 4,
  },
  sheetScroll: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 16,
  },
  emptyText: {
    color: Colors.textSecondary,
    fontSize: 15,
    textAlign: 'center',
    marginTop: 40,
  },
  miniCard: {
    backgroundColor: Colors.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  miniCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  playerName: {
    fontSize: 16,
    fontWeight: '600',
    color: Colors.text,
  },
  resultText: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  miniCardBody: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  propText: {
    fontSize: 14,
    color: Colors.textSecondary,
    textTransform: 'capitalize',
  },
  lineText: {
    fontSize: 14,
    fontWeight: '600',
    color: Colors.text,
  },
  scoreText: {
    fontSize: 12,
    color: Colors.textTertiary,
    marginTop: 8,
  }
});
