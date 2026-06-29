import React from 'react';

export default function SwipeablePickRow({
  onDelete: _onDelete,
  children,
}: {
  onDelete: () => void;
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
