import React, { useState } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity, Pressable } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Pick } from '@/lib/api';

interface SocialFeedProps {
  picks: Pick[];
  onLikePress?: (pick: Pick) => void;
  onSharePress?: (pick: Pick) => void;
  onCommentPress?: (pick: Pick) => void;
}

const PickCard = ({ 
  pick,
  onLikePress,
  onSharePress,
  onCommentPress
}: { 
  pick: Pick;
  onLikePress?: (pick: Pick) => void;
  onSharePress?: (pick: Pick) => void;
  onCommentPress?: (pick: Pick) => void;
}) => {
  const [liked, setLiked] = useState(false);
  const [likesCount, setLikesCount] = useState(Math.floor(Math.random() * 200) + 12);
  const commentCount = Math.floor(Math.random() * 45);

  const handleLike = () => {
    setLiked(!liked);
    setLikesCount(prev => liked ? prev - 1 : prev + 1);
    if (onLikePress) onLikePress(pick);
  };

  const getResultColor = (result?: string) => {
    if (!result) return '#52525B'; // zinc-600
    const lower = result.toLowerCase();
    if (lower === 'win' || lower === 'won') return '#34D399'; // emerald-400
    if (lower === 'loss' || lower === 'lost') return '#F87171'; // red-400
    if (lower === 'push') return '#FBBF24'; // amber-400
    return '#A1A1AA'; // zinc-400
  };

  const getResultBg = (result?: string) => {
    if (!result) return 'rgba(82, 82, 91, 0.15)';
    const lower = result.toLowerCase();
    if (lower === 'win' || lower === 'won') return 'rgba(52, 211, 153, 0.15)';
    if (lower === 'loss' || lower === 'lost') return 'rgba(248, 113, 113, 0.15)';
    if (lower === 'push') return 'rgba(251, 191, 36, 0.15)';
    return 'rgba(161, 161, 170, 0.15)';
  };

  return (
    <View style={styles.card}>
      {/* Header Info */}
      <View style={styles.header}>
        <View style={styles.userSection}>
          <View style={styles.avatar}>
            <Ionicons name="person" size={14} color="#71717A" />
          </View>
          <View style={styles.userText}>
            <Text style={styles.username}>@sharp_bettor</Text>
            <Text style={styles.timestamp}>Just now</Text>
          </View>
        </View>
        <TouchableOpacity style={styles.moreButton}>
          <Ionicons name="ellipsis-horizontal" size={18} color="#71717A" />
        </TouchableOpacity>
      </View>

      {/* Pick Details Box */}
      <View style={styles.pickBox}>
        <View style={styles.pickHeader}>
          <View style={styles.playerInfo}>
            <Text style={styles.playerName} numberOfLines={1}>{pick.playerName}</Text>
            {(pick.teamName || pick.opponentName) && (
              <Text style={styles.matchup}>
                {pick.teamName || 'N/A'} {pick.opponentName ? `vs ${pick.opponentName}` : ''}
              </Text>
            )}
          </View>
          {pick.result && (
            <View style={[styles.resultBadge, { backgroundColor: getResultBg(pick.result) }]}>
              <Text style={[styles.resultText, { color: getResultColor(pick.result) }]}>
                {pick.result.toUpperCase()}
              </Text>
            </View>
          )}
        </View>

        <View style={styles.metricsGrid}>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Market</Text>
            <Text style={styles.metricValue}>{pick.propType}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Line</Text>
            <Text style={styles.metricValue}>{pick.line > 0 ? `+${pick.line}` : pick.line}</Text>
          </View>
          {pick.projection !== undefined && (
            <View style={styles.metric}>
              <Text style={styles.metricLabel}>Proj</Text>
              <Text style={styles.metricValue}>{pick.projection}</Text>
            </View>
          )}
        </View>
      </View>

      {/* Comment Section */}
      <View style={styles.commentContainer}>
        <Text style={styles.commentText}>
          "Numbers are looking really sharp here. Projection is giving us a solid edge against this line."
        </Text>
      </View>

      {/* Actions */}
      <View style={styles.actionRow}>
        <Pressable 
          style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]} 
          onPress={handleLike}
        >
          <Ionicons 
            name={liked ? "heart" : "heart-outline"} 
            size={20} 
            color={liked ? "#F43F5E" : "#A1A1AA"} 
          />
          <Text style={[styles.actionText, liked && styles.actionTextLiked]}>
            {likesCount}
          </Text>
        </Pressable>

        <Pressable 
          style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
          onPress={() => onCommentPress && onCommentPress(pick)}
        >
          <Ionicons name="chatbubble-outline" size={18} color="#A1A1AA" />
          <Text style={styles.actionText}>{commentCount}</Text>
        </Pressable>

        <Pressable 
          style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
          onPress={() => onSharePress && onSharePress(pick)}
        >
          <Ionicons name="share-outline" size={20} color="#A1A1AA" />
          <Text style={styles.actionText}>Share</Text>
        </Pressable>
      </View>
    </View>
  );
};

export default function SocialFeed({ 
  picks, 
  onLikePress, 
  onSharePress, 
  onCommentPress 
}: SocialFeedProps) {
  if (!picks || picks.length === 0) {
    return (
      <View style={styles.emptyState}>
        <Ionicons name="documents-outline" size={48} color="#3F3F46" />
        <Text style={styles.emptyTitle}>No Activity</Text>
        <Text style={styles.emptySub}>When people post picks, they'll show up here.</Text>
      </View>
    );
  }

  return (
    <FlatList
      data={picks}
      keyExtractor={(item, index) => item.id || item._id || item.pickId || index.toString()}
      renderItem={({ item }) => (
        <PickCard 
          pick={item} 
          onLikePress={onLikePress}
          onSharePress={onSharePress}
          onCommentPress={onCommentPress}
        />
      )}
      contentContainerStyle={styles.listContainer}
      showsVerticalScrollIndicator={false}
    />
  );
}

const styles = StyleSheet.create({
  listContainer: {
    padding: 16,
    paddingBottom: 32,
    backgroundColor: '#000000',
  },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    backgroundColor: '#000000',
  },
  emptyTitle: {
    color: '#FAFAFA',
    fontSize: 18,
    fontWeight: '600',
    marginTop: 16,
    marginBottom: 8,
  },
  emptySub: {
    color: '#A1A1AA',
    fontSize: 14,
    textAlign: 'center',
  },
  card: {
    backgroundColor: '#09090B',
    borderRadius: 20,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#27272A',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  userSection: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  avatar: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: '#27272A',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  userText: {
    justifyContent: 'center',
  },
  username: {
    color: '#FAFAFA',
    fontSize: 15,
    fontWeight: '600',
    letterSpacing: -0.2,
  },
  timestamp: {
    color: '#71717A',
    fontSize: 12,
    marginTop: 2,
  },
  moreButton: {
    padding: 4,
  },
  pickBox: {
    backgroundColor: '#18181B',
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#27272A',
  },
  pickHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 16,
  },
  playerInfo: {
    flex: 1,
    paddingRight: 12,
  },
  playerName: {
    color: '#FAFAFA',
    fontSize: 18,
    fontWeight: '700',
    letterSpacing: -0.4,
    marginBottom: 4,
  },
  matchup: {
    color: '#A1A1AA',
    fontSize: 13,
  },
  resultBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  resultText: {
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  metricsGrid: {
    flexDirection: 'row',
    gap: 24,
  },
  metric: {
    flex: 1,
  },
  metricLabel: {
    color: '#71717A',
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 4,
    fontWeight: '600',
  },
  metricValue: {
    color: '#FAFAFA',
    fontSize: 16,
    fontWeight: '600',
  },
  commentContainer: {
    marginBottom: 16,
  },
  commentText: {
    color: '#D4D4D8',
    fontSize: 15,
    lineHeight: 22,
  },
  actionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: '#27272A',
  },
  actionButton: {
    flexDirection: 'row',
    alignItems: 'center',
    marginRight: 28,
    paddingVertical: 4,
  },
  actionButtonPressed: {
    opacity: 0.7,
  },
  actionText: {
    color: '#A1A1AA',
    fontSize: 14,
    fontWeight: '500',
    marginLeft: 6,
  },
  actionTextLiked: {
    color: '#F43F5E',
  },
});
