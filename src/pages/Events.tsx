import { useState, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Calendar, Clock, MapPin, Users, Mail, ChevronLeft, ChevronRight } from 'lucide-react';
import { format, startOfMonth, endOfMonth, eachDayOfInterval, isSameMonth, isSameDay, addMonths, subMonths } from 'date-fns';

interface Event {
  id: string;
  title: string;
  description: string;
  date: Date;
  time: string;
  duration: string;
  location: string;
  type: 'Workshop' | 'Webinar' | 'Office Hours' | 'Community Meetup';
  attendees: number;
  maxAttendees?: number;
}

// Helper function to create dates relative to today
const getDate = (daysFromToday: number, hours: number = 9, minutes: number = 0) => {
  const date = new Date();
  date.setDate(date.getDate() + daysFromToday);
  date.setHours(hours, minutes, 0, 0);
  return date;
};

const mockEvents: Event[] = [
  {
    id: '1',
    title: 'Databricks Fundamentals Workshop',
    description: 'Hands-on workshop covering workspace navigation, notebooks, and basic operations.',
    date: getDate(3, 9), // 3 days from today at 9 AM
    time: '9:00 AM',
    duration: '4 hours',
    location: 'Building Q, Room 201',
    type: 'Workshop',
    attendees: 12,
    maxAttendees: 20,
  },
  {
    id: '2',
    title: 'Data Governance Best Practices',
    description: 'Learn about access controls, catalog management, and compliance requirements.',
    date: getDate(7, 14), // 7 days from today at 2 PM
    time: '2:00 PM',
    duration: '2 hours',
    location: 'Virtual (Teams)',
    type: 'Webinar',
    attendees: 45,
    maxAttendees: 100,
  },
  {
    id: '3',
    title: 'Platform Admin Office Hours',
    description: 'Drop-in session for platform administrators to ask questions and get support.',
    date: getDate(10, 10), // 10 days from today at 10 AM
    time: '10:00 AM',
    duration: '1 hour',
    location: 'Virtual (Teams)',
    type: 'Office Hours',
    attendees: 8,
  },
  {
    id: '4',
    title: 'Databricks Community Meetup',
    description: 'Monthly meetup for Databricks users to share experiences and best practices.',
    date: getDate(14, 15), // 14 days from today at 3 PM
    time: '3:00 PM',
    duration: '1.5 hours',
    location: 'Building Q, Conference Room A',
    type: 'Community Meetup',
    attendees: 25,
    maxAttendees: 40,
  },
  {
    id: '5',
    title: 'Advanced Analytics Deep Dive',
    description: 'Deep dive into ML workflows and model deployment patterns.',
    date: getDate(21, 9), // 21 days from today at 9 AM
    time: '9:00 AM',
    duration: '6 hours',
    location: 'Building Q, Room 301',
    type: 'Workshop',
    attendees: 0,
    maxAttendees: 15,
  },
  {
    id: '6',
    title: 'Delta Sharing Workshop',
    description: 'Learn how to securely share data using Delta Sharing.',
    date: getDate(28, 13), // 28 days from today at 1 PM
    time: '1:00 PM',
    duration: '3 hours',
    location: 'Virtual (Teams)',
    type: 'Workshop',
    attendees: 18,
    maxAttendees: 30,
  },
  {
    id: '7',
    title: 'Workspace Provisioning Training',
    description: 'Learn how to provision and manage Databricks workspaces effectively.',
    date: getDate(5, 11), // 5 days from today at 11 AM
    time: '11:00 AM',
    duration: '3 hours',
    location: 'Virtual (Teams)',
    type: 'Workshop',
    attendees: 15,
    maxAttendees: 25,
  },
  {
    id: '8',
    title: 'MLOps Best Practices',
    description: 'Explore MLOps patterns and best practices for model deployment.',
    date: getDate(12, 14), // 12 days from today at 2 PM
    time: '2:00 PM',
    duration: '2 hours',
    location: 'Building Q, Room 202',
    type: 'Webinar',
    attendees: 32,
    maxAttendees: 50,
  },
  {
    id: '9',
    title: 'Data Engineering Office Hours',
    description: 'Q&A session for data engineers working with Databricks pipelines.',
    date: getDate(17, 10), // 17 days from today at 10 AM
    time: '10:00 AM',
    duration: '1 hour',
    location: 'Virtual (Teams)',
    type: 'Office Hours',
    attendees: 12,
  },
  {
    id: '10',
    title: 'Security & Compliance Roundtable',
    description: 'Discussion on security best practices and compliance requirements.',
    date: getDate(24, 15), // 24 days from today at 3 PM
    time: '3:00 PM',
    duration: '1.5 hours',
    location: 'Building Q, Conference Room B',
    type: 'Community Meetup',
    attendees: 18,
    maxAttendees: 30,
  },
];

const eventTypeColors = {
  Workshop: 'bg-blue-100 text-blue-800',
  Webinar: 'bg-green-100 text-green-800',
  'Office Hours': 'bg-yellow-100 text-yellow-800',
  'Community Meetup': 'bg-purple-100 text-purple-800',
};

export function Events() {
  const [currentDate, setCurrentDate] = useState(new Date());
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);
  const [highlightedEventId, setHighlightedEventId] = useState<string | null>(null);
  const [selectedFilter, setSelectedFilter] = useState<Event['type'] | 'All'>('All');
  const eventRefs = useRef<{ [key: string]: HTMLDivElement | null }>({});

  const monthStart = startOfMonth(currentDate);
  const monthEnd = endOfMonth(currentDate);
  const daysInMonth = eachDayOfInterval({ start: monthStart, end: monthEnd });

  const getEventsForDate = (date: Date) => {
    return mockEvents.filter((event) => isSameDay(event.date, date));
  };

  const handleInviteMe = () => {
    // Mock: In real app, this would send an invitation
    alert('Invitation sent! You will receive a calendar invite shortly.');
  };

  const handleDateClick = (date: Date) => {
    setSelectedDate(date);
    const eventsForDate = getEventsForDate(date);
    
    if (eventsForDate.length > 0) {
      // Scroll to the first event and highlight it
      const firstEvent = eventsForDate[0];
      const eventElement = eventRefs.current[firstEvent.id];
      
      if (eventElement) {
        eventElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
        
        // Flash green
        setHighlightedEventId(firstEvent.id);
        setTimeout(() => {
          setHighlightedEventId(null);
        }, 2000); // Flash for 2 seconds
      }
    }
  };

  const nextMonth = () => setCurrentDate(addMonths(currentDate, 1));
  const prevMonth = () => setCurrentDate(subMonths(currentDate, 1));

  const upcomingEvents = mockEvents
    .filter((event) => event.date >= new Date())
    .filter((event) => selectedFilter === 'All' || event.type === selectedFilter)
    .sort((a, b) => a.date.getTime() - b.date.getTime());

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Upcoming Events</h1>
        <p className="text-gray-600 mb-4">Browse and register for upcoming Databricks training sessions and community events</p>
        
        {/* Filter Chips */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setSelectedFilter('All')}
            className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
              selectedFilter === 'All'
                ? 'bg-primary text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            All
          </button>
          {Object.keys(eventTypeColors).map((type) => (
            <button
              key={type}
              onClick={() => setSelectedFilter(type as Event['type'])}
              className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                selectedFilter === type
                  ? `${eventTypeColors[type as keyof typeof eventTypeColors]} border-2 border-gray-900`
                  : `${eventTypeColors[type as keyof typeof eventTypeColors]} hover:opacity-80`
              }`}
            >
              {type}
            </button>
          ))}
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Calendar */}
        <div className="lg:col-span-1">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-lg">Calendar</CardTitle>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={prevMonth}
                    className="p-1 h-8 w-8"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={nextMonth}
                    className="p-1 h-8 w-8"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </Button>
                </div>
              </div>
              <p className="text-sm text-gray-600 mt-1">
                {format(currentDate, 'MMMM yyyy')}
              </p>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-7 gap-1 mb-2">
                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day) => (
                  <div key={day} className="text-center text-xs font-semibold text-gray-500 py-2">
                    {day}
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-1">
                {/* Empty cells for days before month start */}
                {Array.from({ length: monthStart.getDay() }).map((_, i) => (
                  <div key={`empty-${i}`} className="aspect-square" />
                ))}
                {daysInMonth.map((day) => {
                  const dayEvents = getEventsForDate(day);
                  const isSelected = selectedDate && isSameDay(day, selectedDate);
                  const isToday = isSameDay(day, new Date());
                  const hasEvents = dayEvents.length > 0;

                  return (
                    <button
                      key={day.toISOString()}
                      onClick={() => handleDateClick(day)}
                      className={`
                        aspect-square rounded-md text-sm transition-colors
                        ${isSelected ? 'bg-primary text-white' : ''}
                        ${!isSelected && isToday ? 'bg-primary/10 text-primary font-semibold' : ''}
                        ${!isSelected && !isToday && hasEvents ? 'bg-blue-50 text-blue-700 hover:bg-blue-100' : ''}
                        ${!isSelected && !isToday && !hasEvents ? 'text-gray-700 hover:bg-gray-100' : ''}
                        ${!isSameMonth(day, currentDate) ? 'opacity-30' : ''}
                      `}
                    >
                      {format(day, 'd')}
                      {hasEvents && !isSelected && (
                        <div className="w-1 h-1 bg-primary rounded-full mx-auto mt-0.5" />
                      )}
                    </button>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Events List */}
        <div className="lg:col-span-2 space-y-4 overflow-y-auto max-h-[calc(100vh-250px)] pr-2">
          <h2 className="text-xl font-semibold text-gray-900 sticky top-0 bg-white pb-2 z-10">
            {selectedFilter === 'All' ? 'All Upcoming Events' : `${selectedFilter} Events`}
            {upcomingEvents.length > 0 && (
              <span className="text-sm font-normal text-gray-500 ml-2">({upcomingEvents.length})</span>
            )}
          </h2>
          {upcomingEvents.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <p className="text-gray-600">No {selectedFilter === 'All' ? '' : selectedFilter.toLowerCase()} events found.</p>
              </CardContent>
            </Card>
          ) : (
            upcomingEvents.map((event) => (
            <div
              key={event.id}
              ref={(el) => {
                eventRefs.current[event.id] = el;
              }}
              className={`transition-all duration-500 ${
                highlightedEventId === event.id
                  ? 'ring-4 ring-green-500 bg-green-50 rounded-lg'
                  : ''
              }`}
            >
              <Card
                className={highlightedEventId === event.id ? 'bg-green-50' : ''}
              >
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${eventTypeColors[event.type]}`}>
                        {event.type}
                      </span>
                    </div>
                    <CardTitle className="text-lg">{event.title}</CardTitle>
                    <p className="text-sm text-gray-600 mt-2">{event.description}</p>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid md:grid-cols-2 gap-4 mb-4">
                  <div className="flex items-center gap-2 text-sm text-gray-600">
                    <Calendar className="w-4 h-4" />
                    <span>{format(event.date, 'EEEE, MMMM d, yyyy')}</span>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-gray-600">
                    <Clock className="w-4 h-4" />
                    <span>{event.time} ({event.duration})</span>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-gray-600">
                    <MapPin className="w-4 h-4" />
                    <span>{event.location}</span>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-gray-600">
                    <Users className="w-4 h-4" />
                    <span>
                      {event.attendees} {event.maxAttendees ? `/ ${event.maxAttendees}` : ''} attendees
                    </span>
                  </div>
                </div>
                <Button
                  onClick={handleInviteMe}
                  className="w-full"
                >
                  <Mail className="w-4 h-4 mr-2" />
                  Invite Me
                </Button>
              </CardContent>
            </Card>
            </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

