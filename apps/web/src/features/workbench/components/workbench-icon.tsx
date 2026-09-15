import type { SVGProps } from 'react';

type IconName = 'left' | 'right' | 'refresh' | 'send' | 'chevron';
const paths: Record<IconName, string> = {
    left: 'M9 4v16M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z',
    right: 'M15 4v16M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z',
    refresh: 'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1',
    send: 'M12 19V5m-6 6 6-6 6 6',
    chevron: 'm9 5 7 7-7 7',
};
export default function WorkbenchIcon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
    return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name]} /></svg>;
}
